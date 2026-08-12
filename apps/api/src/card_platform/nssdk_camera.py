import ctypes
import platform
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from card_platform.config import Settings
from card_platform.schemas import (
    ScannerCameraControls,
    ScannerCameraControlUpdate,
    ScannerCameraProperty,
)


NSSDK_SUCCESS = 0
NSSDK_ACCESS_SET = 0x02
NSSDK_PROP_GAIN = 7
NSSDK_PROP_EXPOSURE_MODE = 10
NSSDK_PROP_EXPOSURE_TIME = 11


class NssdkUnavailableError(RuntimeError):
    pass


class NssdkControlError(RuntimeError):
    pass


class NssdkPropertyInfo(ctypes.Structure):
    _fields_ = [
        ("prop_id", ctypes.c_int),
        ("type", ctypes.c_int),
        ("access", ctypes.c_int),
        ("min_value", ctypes.c_double),
        ("max_value", ctypes.c_double),
        ("step", ctypes.c_double),
        ("default_value", ctypes.c_double),
    ]


def default_nssdk_library_path() -> Path:
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "CardPlatform"
        / "nssdk.framework"
        / "Versions"
        / "A"
        / "nssdk"
    )


class NssdkCameraController:
    _lock = threading.Lock()

    def __init__(self, settings: Settings) -> None:
        configured = settings.scanner_nssdk_library_path
        self.library_path = Path(configured).expanduser() if configured else default_nssdk_library_path()

    def read(self) -> ScannerCameraControls:
        with self._lock, self._opened_device() as (library, device, device_name):
            return self._read_controls(library, device, device_name)

    def update(self, values: ScannerCameraControlUpdate) -> ScannerCameraControls:
        with self._lock, self._opened_device() as (library, device, device_name):
            if values.exposure_mode is not None:
                self._set_property(
                    library,
                    device,
                    NSSDK_PROP_EXPOSURE_MODE,
                    0 if values.exposure_mode == "auto" else 1,
                )
            if values.exposure_time is not None:
                mode = self._get_property(library, device, NSSDK_PROP_EXPOSURE_MODE)
                if round(mode) != 1:
                    raise ValueError("手动调整曝光前，请先切换到手动曝光模式")
                exposure = self._property(library, device, NSSDK_PROP_EXPOSURE_TIME)
                if not exposure.minimum <= values.exposure_time <= exposure.maximum:
                    raise ValueError(
                        f"曝光值必须在 {exposure.minimum:g} 到 {exposure.maximum:g} 之间"
                    )
                self._set_property(
                    library,
                    device,
                    NSSDK_PROP_EXPOSURE_TIME,
                    values.exposure_time,
                )
            return self._read_controls(library, device, device_name)

    @contextmanager
    def _opened_device(self) -> Iterator[tuple[ctypes.CDLL, ctypes.c_void_p, str]]:
        if platform.system() != "Darwin":
            raise NssdkUnavailableError("NSSDK 硬件曝光控制目前只在 macOS 上启用")
        if not self.library_path.is_file():
            raise NssdkUnavailableError(
                f"未安装 NSSDK：{self.library_path}；请运行 scripts/install_nssdk_macos.sh"
            )
        try:
            library = ctypes.CDLL(str(self.library_path))
        except OSError as error:
            raise NssdkUnavailableError(f"NSSDK 加载失败：{error}") from error
        self._configure_library(library)
        initialized = library.nssdk_initialize()
        if initialized != NSSDK_SUCCESS:
            raise NssdkControlError(f"NSSDK 初始化失败（错误码 {initialized}）")
        device = None
        try:
            for index in range(library.nssdk_get_device_count()):
                candidate = library.nssdk_get_device(index)
                if not candidate:
                    continue
                raw_name = library.nssdk_device_get_name(candidate)
                name = raw_name.decode("utf-8", errors="replace") if raw_name else ""
                if "NETUM" in name.upper() or "KORNVISION" in name.upper():
                    device = candidate
                    device_name = name or "NETUM Camera"
                    break
            if not device:
                raise NssdkUnavailableError("未发现已连接的 NETUM 高拍仪")
            opened = library.nssdk_open_device(device)
            if opened != NSSDK_SUCCESS:
                raise NssdkControlError(f"高拍仪控制通道打开失败（错误码 {opened}）")
            try:
                yield library, device, device_name
            finally:
                library.nssdk_close_device(device)
        finally:
            library.nssdk_uninitialize()

    @staticmethod
    def _configure_library(library: ctypes.CDLL) -> None:
        library.nssdk_initialize.argtypes = []
        library.nssdk_initialize.restype = ctypes.c_int
        library.nssdk_uninitialize.argtypes = []
        library.nssdk_uninitialize.restype = ctypes.c_int
        library.nssdk_get_device_count.argtypes = []
        library.nssdk_get_device_count.restype = ctypes.c_int
        library.nssdk_get_device.argtypes = [ctypes.c_int]
        library.nssdk_get_device.restype = ctypes.c_void_p
        library.nssdk_device_get_name.argtypes = [ctypes.c_void_p]
        library.nssdk_device_get_name.restype = ctypes.c_char_p
        library.nssdk_open_device.argtypes = [ctypes.c_void_p]
        library.nssdk_open_device.restype = ctypes.c_int
        library.nssdk_close_device.argtypes = [ctypes.c_void_p]
        library.nssdk_close_device.restype = ctypes.c_int
        library.nssdk_get_property.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_double),
        ]
        library.nssdk_get_property.restype = ctypes.c_int
        library.nssdk_set_property.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_double]
        library.nssdk_set_property.restype = ctypes.c_int
        library.nssdk_get_property_info.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.POINTER(NssdkPropertyInfo),
        ]
        library.nssdk_get_property_info.restype = ctypes.c_int

    def _read_controls(
        self,
        library: ctypes.CDLL,
        device: ctypes.c_void_p,
        device_name: str,
    ) -> ScannerCameraControls:
        mode = self._get_property(library, device, NSSDK_PROP_EXPOSURE_MODE)
        gain: ScannerCameraProperty | None
        try:
            gain = self._property(library, device, NSSDK_PROP_GAIN)
        except NssdkControlError:
            gain = None
        return ScannerCameraControls(
            device_name=device_name,
            exposure_mode="manual" if round(mode) == 1 else "auto",
            exposure_time=self._property(library, device, NSSDK_PROP_EXPOSURE_TIME),
            gain=gain,
        )

    def _property(
        self, library: ctypes.CDLL, device: ctypes.c_void_p, property_id: int
    ) -> ScannerCameraProperty:
        info = NssdkPropertyInfo()
        result = library.nssdk_get_property_info(device, property_id, ctypes.byref(info))
        if result != NSSDK_SUCCESS:
            raise NssdkControlError(f"读取相机属性 {property_id} 范围失败（错误码 {result}）")
        return ScannerCameraProperty(
            minimum=info.min_value,
            maximum=info.max_value,
            step=info.step,
            default=info.default_value,
            current=self._get_property(library, device, property_id),
            writable=bool(info.access & NSSDK_ACCESS_SET),
        )

    @staticmethod
    def _get_property(library: ctypes.CDLL, device: ctypes.c_void_p, property_id: int) -> float:
        value = ctypes.c_double()
        result = library.nssdk_get_property(device, property_id, ctypes.byref(value))
        if result != NSSDK_SUCCESS:
            raise NssdkControlError(f"读取相机属性 {property_id} 失败（错误码 {result}）")
        return value.value

    @staticmethod
    def _set_property(
        library: ctypes.CDLL,
        device: ctypes.c_void_p,
        property_id: int,
        value: float,
    ) -> None:
        result = library.nssdk_set_property(device, property_id, value)
        if result != NSSDK_SUCCESS:
            raise NssdkControlError(f"设置相机属性 {property_id} 失败（错误码 {result}）")
