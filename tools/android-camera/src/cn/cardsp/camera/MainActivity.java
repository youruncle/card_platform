package cn.cardsp.camera;

import android.Manifest;
import android.app.Activity;
import android.os.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.graphics.*;
import android.hardware.camera2.*;
import android.hardware.camera2.params.StreamConfigurationMap;
import android.hardware.camera2.params.MeteringRectangle;
import android.media.ImageReader;
import android.media.Image;
import android.net.LocalServerSocket;
import android.net.LocalSocket;
import android.util.Range;
import android.util.Size;
import android.view.*;
import android.widget.*;
import org.json.JSONObject;
import java.io.*;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.*;

/** Foreground USB camera: no Internet, storage, microphone, or background service. */
public final class MainActivity extends Activity {
 private TextureView texture;
 private FrameLayout root;
 private TextView status;
 private Handler cameraHandler;
 private HandlerThread cameraThread;
 private CameraDevice camera;
 private CameraCaptureSession session;
 private CaptureRequest.Builder repeating;
 private ImageReader jpeg;
 private CameraCharacteristics characteristics;
 private boolean useRatio,opening=false,resumed=false;
 private Size previewSize;
 private volatile float displayedZoom=Float.NaN,actualZoom=Float.NaN,requestedZoom=1,minZoom=1,maxZoom=3;
 private volatile String token="",lastError="相机正在启动";
 private volatile boolean stopped=false;
 private volatile CompletableFuture<Float> pendingZoom;
 private volatile CompletableFuture<Integer> pendingFocus;
 private volatile String pendingFocusTag;
 private volatile float[] focusPoint;
 private volatile int afState=0;
 private volatile CompletableFuture<byte[]> pendingPhoto;
 private volatile long pendingPhotoTimestamp=-1,matchedFrameTimestamp=-1,displayedTimestamp=-1,focusedFrameTimestamp=-1,lastPreviewTimestamp=-1,lastPhotoTimestamp=-1;
 private final Set<String> recentIds=Collections.synchronizedSet(new LinkedHashSet<String>());
 private volatile CompletableFuture<Void> ready=new CompletableFuture<>();
 private LocalServerSocket server;

 @Override public void onCreate(Bundle saved) {
  super.onCreate(saved);getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON|WindowManager.LayoutParams.FLAG_FULLSCREEN);
  token=getIntent().getStringExtra("cardsp_token");if(token==null)token="";
  root=new FrameLayout(this);root.setBackgroundColor(Color.BLACK);texture=new TextureView(this);status=new TextView(this);status.setTextColor(Color.WHITE);status.setPadding(16,8,16,8);status.setBackgroundColor(0x99000000);
  root.addView(texture,new FrameLayout.LayoutParams(1,1,Gravity.CENTER));root.addView(status,new FrameLayout.LayoutParams(-1,-2,Gravity.BOTTOM));setContentView(root);
  cameraThread=new HandlerThread("cardsp-camera");cameraThread.start();cameraHandler=new Handler(cameraThread.getLooper());
  texture.setSurfaceTextureListener(new TextureView.SurfaceTextureListener(){public void onSurfaceTextureAvailable(SurfaceTexture s,int w,int h){fit();open();}public void onSurfaceTextureSizeChanged(SurfaceTexture s,int w,int h){fit();}public boolean onSurfaceTextureDestroyed(SurfaceTexture s){closeCamera();return true;}public void onSurfaceTextureUpdated(SurfaceTexture s){displayedTimestamp=s.getTimestamp();}});
  root.addOnLayoutChangeListener((v,l,t,r,b,ol,ot,or,ob)->fit());
  if(checkSelfPermission(Manifest.permission.CAMERA)!=PackageManager.PERMISSION_GRANTED)requestPermissions(new String[]{Manifest.permission.CAMERA},17);
  new Thread(this::serve,"cardsp-usb").start();message("仅通过 USB 连接电脑 · 等待相机权限");
 }
 @Override protected void onNewIntent(Intent intent){super.onNewIntent(intent);setIntent(intent);String next=intent.getStringExtra("cardsp_token");if(next!=null&&!next.equals(token)){token=next;recentIds.clear();}if(texture!=null&&texture.isAvailable())open();}
 @Override protected void onResume(){super.onResume();resumed=true;if(texture!=null&&texture.isAvailable())open();}
 @Override protected void onPause(){resumed=false;closeCamera();super.onPause();}
 @Override public void onRequestPermissionsResult(int code,String[] permissions,int[] grants){super.onRequestPermissionsResult(code,permissions,grants);if(code==17&&grants.length>0&&grants[0]==PackageManager.PERMISSION_GRANTED)open();else message("相机权限未授予，请在应用设置允许相机后重试");}
 @Override protected void onDestroy(){stopped=true;closeCamera();try{if(server!=null)server.close();}catch(IOException ignored){}cameraThread.quitSafely();super.onDestroy();}
 private void message(String text){lastError=text;runOnUiThread(()->status.setText(text));}
 private void fit(){if(root==null||root.getWidth()==0)return;int w=Math.min(root.getWidth(),root.getHeight()*4/3),h=w*3/4;ViewGroup.LayoutParams p=texture.getLayoutParams();if(p.width!=w||p.height!=h){p.width=w;p.height=h;texture.setLayoutParams(p);}Matrix matrix=new Matrix();int rotation=getWindowManager().getDefaultDisplay().getRotation();
 if(previewSize!=null&&(rotation==Surface.ROTATION_90||rotation==Surface.ROTATION_270)){
  RectF view=new RectF(0,0,w,h),buffer=new RectF(0,0,previewSize.getHeight(),previewSize.getWidth());float cx=view.centerX(),cy=view.centerY();buffer.offset(cx-buffer.centerX(),cy-buffer.centerY());
  matrix.setRectToRect(view,buffer,Matrix.ScaleToFit.FILL);float scale=Math.max((float)h/previewSize.getHeight(),(float)w/previewSize.getWidth());matrix.postScale(scale,scale,cx,cy);matrix.postRotate(90*(rotation-2),cx,cy);
 }else if(rotation==Surface.ROTATION_180)matrix.postRotate(180,w/2f,h/2f);texture.setTransform(matrix);}
 private synchronized void open(){
  if(camera!=null||opening||!resumed||stopped||!texture.isAvailable()||checkSelfPermission(Manifest.permission.CAMERA)!=PackageManager.PERMISSION_GRANTED)return;
  try{
   CameraManager manager=(CameraManager)getSystemService(CAMERA_SERVICE);String id=null;
   double best=-1;String[] ids=manager.getCameraIdList();Arrays.sort(ids);
   for(String candidate:ids){CameraCharacteristics c=manager.getCameraCharacteristics(candidate);android.util.SizeF physical=c.get(CameraCharacteristics.SENSOR_INFO_PHYSICAL_SIZE);Float focus=c.get(CameraCharacteristics.LENS_INFO_MINIMUM_FOCUS_DISTANCE);double score=ZoomPolicy.cameraScore(c.get(CameraCharacteristics.LENS_FACING)==CameraCharacteristics.LENS_FACING_BACK,focus==null?0:focus,physical==null?0:physical.getWidth(),physical==null?0:physical.getHeight());if(score>best){best=score;id=candidate;}}
   if(id==null)throw new IllegalStateException("未找到后置相机");characteristics=manager.getCameraCharacteristics(id);
   Range<Float> range=characteristics.get(CameraCharacteristics.CONTROL_ZOOM_RATIO_RANGE);useRatio=range!=null&&characteristics.getAvailableCaptureRequestKeys().contains(CaptureRequest.CONTROL_ZOOM_RATIO);
   minZoom=useRatio?Math.max(1,range.getLower()):1;Float digital=characteristics.get(CameraCharacteristics.SCALER_AVAILABLE_MAX_DIGITAL_ZOOM);maxZoom=Math.min(3,useRatio?range.getUpper():(digital==null?1:digital));requestedZoom=Math.max(minZoom,Math.min(maxZoom,requestedZoom));
   ready=new CompletableFuture<>();opening=true;manager.openCamera(id,new CameraDevice.StateCallback(){public void onOpened(CameraDevice device){opening=false;if(!resumed){device.close();return;}camera=device;configure();}public void onDisconnected(CameraDevice device){opening=false;device.close();camera=null;failPending("USB相机已断开");}public void onError(CameraDevice device,int error){opening=false;device.close();camera=null;failPending("相机启动失败（"+error+"）");}},cameraHandler);
  }catch(Exception e){opening=false;failPending("相机打开失败，请退出其他相机应用后重试");}
 }
 private void configure(){try{
  StreamConfigurationMap streams=characteristics.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);Size still=null,preview=null;
  for(Size s:streams.getOutputSizes(ImageFormat.JPEG))if(Math.abs((double)s.getWidth()/s.getHeight()-4d/3)<.02&&(long)s.getWidth()*s.getHeight()<=20000000&&(still==null||(long)s.getWidth()*s.getHeight()>(long)still.getWidth()*still.getHeight()))still=s;
  for(Size s:streams.getOutputSizes(SurfaceTexture.class))if(Math.abs((double)s.getWidth()/s.getHeight()-4d/3)<.02&&s.getWidth()<=1600&&(preview==null||s.getWidth()>preview.getWidth()))preview=s;
  if(still==null||preview==null)throw new IllegalStateException("相机不支持4:3照片");
  jpeg=ImageReader.newInstance(still.getWidth(),still.getHeight(),ImageFormat.JPEG,2);
  jpeg.setOnImageAvailableListener(reader->{try(Image image=reader.acquireNextImage()){if(image==null)return;ByteBuffer buffer=image.getPlanes()[0].getBuffer();byte[] bytes=new byte[buffer.remaining()];buffer.get(bytes);CompletableFuture<byte[]> f=pendingPhoto;if(f!=null&&pendingPhotoTimestamp>0&&image.getTimestamp()==pendingPhotoTimestamp){pendingPhoto=null;lastPhotoTimestamp=image.getTimestamp();f.complete(bytes);}}catch(Exception e){CompletableFuture<byte[]> f=pendingPhoto;pendingPhoto=null;if(f!=null)f.completeExceptionally(e);}},cameraHandler);
  previewSize=preview;runOnUiThread(this::fit);SurfaceTexture surface=texture.getSurfaceTexture();surface.setDefaultBufferSize(preview.getWidth(),preview.getHeight());Surface output=new Surface(surface);
  repeating=camera.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW);repeating.addTarget(output);settings(repeating);
  camera.createCaptureSession(Arrays.asList(output,jpeg.getSurface()),new CameraCaptureSession.StateCallback(){public void onConfigured(CameraCaptureSession created){session=created;try{session.setRepeatingRequest(repeating.build(),results,cameraHandler);}catch(Exception e){failPending("相机预览启动失败");}}public void onConfigureFailed(CameraCaptureSession s){failPending("相机预览配置失败");}},cameraHandler);
 }catch(Exception e){failPending("相机配置失败，请重新打开组件");}}
 private void settings(CaptureRequest.Builder b){
  b.set(CaptureRequest.CONTROL_MODE,CaptureRequest.CONTROL_MODE_AUTO);b.set(CaptureRequest.CONTROL_AF_MODE,focusPoint==null?CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE:CaptureRequest.CONTROL_AF_MODE_AUTO);b.set(CaptureRequest.FLASH_MODE,CaptureRequest.FLASH_MODE_OFF);
  if(focusPoint!=null){
   Rect a=characteristics.get(CameraCharacteristics.SENSOR_INFO_ACTIVE_ARRAY_SIZE),region=a;
   if(!useRatio){int cw=Math.round(a.width()/requestedZoom),ch=Math.round(a.height()/requestedZoom);region=new Rect(a.centerX()-cw/2,a.centerY()-ch/2,a.centerX()+cw/2,a.centerY()+ch/2);}
   int rw=Math.max(16,region.width()/7),rh=Math.max(16,region.height()/7),cx=region.left+Math.round(focusPoint[0]*region.width()),cy=region.top+Math.round(focusPoint[1]*region.height());
   int left=Math.max(region.left,Math.min(region.right-rw,cx-rw/2)),top=Math.max(region.top,Math.min(region.bottom-rh,cy-rh/2));MeteringRectangle[] meter={new MeteringRectangle(new Rect(left,top,left+rw,top+rh),1000)};
   Integer af=characteristics.get(CameraCharacteristics.CONTROL_MAX_REGIONS_AF),ae=characteristics.get(CameraCharacteristics.CONTROL_MAX_REGIONS_AE);if(af!=null&&af>0)b.set(CaptureRequest.CONTROL_AF_REGIONS,meter);if(ae!=null&&ae>0)b.set(CaptureRequest.CONTROL_AE_REGIONS,meter);
  }
  if(useRatio)b.set(CaptureRequest.CONTROL_ZOOM_RATIO,requestedZoom);else{Rect a=characteristics.get(CameraCharacteristics.SENSOR_INFO_ACTIVE_ARRAY_SIZE);int w=Math.round(a.width()/requestedZoom),h=Math.round(a.height()/requestedZoom);b.set(CaptureRequest.SCALER_CROP_REGION,new Rect(a.centerX()-w/2,a.centerY()-h/2,a.centerX()+w/2,a.centerY()+h/2));}
 }
 private final CameraCaptureSession.CaptureCallback results=new CameraCaptureSession.CaptureCallback(){
  @Override public void onCaptureCompleted(CameraCaptureSession s,CaptureRequest request,TotalCaptureResult result){
   Float ratio=result.get(CaptureResult.CONTROL_ZOOM_RATIO);
   if(!useRatio||ratio==null){Rect crop=result.get(CaptureResult.SCALER_CROP_REGION),active=characteristics.get(CameraCharacteristics.SENSOR_INFO_ACTIVE_ARRAY_SIZE);ratio=crop==null?Float.NaN:(float)active.width()/crop.width();}
   actualZoom=ratio;Long timestamp=result.get(CaptureResult.SENSOR_TIMESTAMP);boolean matches=Float.isFinite(actualZoom)&&timestamp!=null&&timestamp>0&&ZoomPolicy.same(actualZoom,requestedZoom);
   if(matches)matchedFrameTimestamp=timestamp;
   Integer af=result.get(CaptureResult.CONTROL_AF_STATE);if(af!=null)afState=af;
   CompletableFuture<Integer> focus=pendingFocus;
   if(matches&&focus!=null&&pendingFocusTag!=null&&pendingFocusTag.equals(request.getTag())&&af!=null&&(af==CaptureResult.CONTROL_AF_STATE_FOCUSED_LOCKED||af==CaptureResult.CONTROL_AF_STATE_NOT_FOCUSED_LOCKED)){
    // Commit the locked frame before waking the server thread that snapshots preview requirements.
    focusedFrameTimestamp=timestamp;pendingFocus=null;focus.complete(af);
   }
   if(Float.isFinite(actualZoom)){
    if(!Float.isFinite(displayedZoom)||Math.abs(displayedZoom-actualZoom)>.01){displayedZoom=actualZoom;message(String.format(java.util.Locale.ROOT,"USB 相机 · 实际倍率 %.1f× · 请在电脑操作",actualZoom));}
    ready.complete(null);CompletableFuture<Float> zoom=pendingZoom;if(matches&&zoom!=null){pendingZoom=null;zoom.complete(actualZoom);}
   }
  }
 };
 private void failPending(String text){message(text);ready.completeExceptionally(new IllegalStateException(text));CompletableFuture<Float> z=pendingZoom;pendingZoom=null;if(z!=null)z.completeExceptionally(new IllegalStateException(text));CompletableFuture<byte[]> p=pendingPhoto;pendingPhoto=null;if(p!=null)p.completeExceptionally(new IllegalStateException(text));}
 private synchronized void closeCamera(){if(session!=null){session.close();session=null;}if(camera!=null){camera.close();camera=null;}if(jpeg!=null){jpeg.close();jpeg=null;}actualZoom=Float.NaN;focusPoint=null;focusedFrameTimestamp=-1;afState=0;matchedFrameTimestamp=-1;displayedTimestamp=-1;failPending("相机已暂停，请在电脑重新连接");}
 private JSONObject state()throws Exception{ready.get(10,TimeUnit.SECONDS);return new JSONObject().put("zoom",actualZoom).put("minZoom",minZoom).put("maxZoom",maxZoom).put("protocol",1).put("version","1.0.3").put("afState",afState).put("cameraId",camera.getId()).put("minimumFocusDistance",characteristics.get(CameraCharacteristics.LENS_INFO_MINIMUM_FOCUS_DISTANCE)).put("displayRotation",getWindowManager().getDefaultDisplay().getRotation());}
 private byte[] preview()throws Exception{
  final long required=Math.max(matchedFrameTimestamp,focusPoint==null?-1:focusedFrameTimestamp),deadline=SystemClock.uptimeMillis()+5000;
  CompletableFuture<byte[]> result=new CompletableFuture<>();
  runOnUiThread(new Runnable(){public void run(){try{
   if(!texture.isAvailable())throw new IllegalStateException("取景未就绪");
   if(!ZoomPolicy.previewReady(required,displayedTimestamp)){
    if(SystemClock.uptimeMillis()>=deadline)throw new IllegalStateException("新倍率取景尚未显示");
    texture.postDelayed(this,16);return;
   }
   int rotation=getWindowManager().getDefaultDisplay().getRotation();boolean swap=rotation==Surface.ROTATION_90||rotation==Surface.ROTATION_270;
   lastPreviewTimestamp=displayedTimestamp;Bitmap raw=texture.getBitmap(swap?810:1080,swap?1080:810);if(raw==null)throw new IllegalStateException();Matrix turn=new Matrix();if(swap)turn.postRotate(90*(rotation-2));else if(rotation==Surface.ROTATION_180)turn.postRotate(180);Bitmap bitmap=Bitmap.createBitmap(raw,0,0,raw.getWidth(),raw.getHeight(),turn,true);if(bitmap!=raw)raw.recycle();
   ByteArrayOutputStream stream=new ByteArrayOutputStream();bitmap.compress(Bitmap.CompressFormat.JPEG,85,stream);bitmap.recycle();result.complete(stream.toByteArray());
  }catch(Exception e){result.completeExceptionally(e);}}});return result.get(6,TimeUnit.SECONDS);
 }
 private float zoom(double value)throws Exception{
  final float next=ZoomPolicy.validate(value,minZoom,maxZoom);CompletableFuture<Float> result=new CompletableFuture<>();cameraHandler.post(()->{try{requestedZoom=next;focusPoint=null;focusedFrameTimestamp=-1;matchedFrameTimestamp=-1;pendingZoom=result;settings(repeating);session.setRepeatingRequest(repeating.build(),results,cameraHandler);}catch(Exception e){pendingZoom=null;result.completeExceptionally(e);}});try{return result.get(8,TimeUnit.SECONDS);}finally{if(pendingZoom==result)pendingZoom=null;}
 }
 private int jpegOrientation(){int rotation=getWindowManager().getDefaultDisplay().getRotation(),sensor=characteristics.get(CameraCharacteristics.SENSOR_ORIENTATION);int[] rotations={90,0,270,180};return (rotations[rotation]+sensor+270)%360;}
 private boolean focus(double x,double y)throws Exception{
  Float minimum=characteristics.get(CameraCharacteristics.LENS_INFO_MINIMUM_FOCUS_DISTANCE);if(minimum==null||minimum<=0)return true;
  final float[] point=ZoomPolicy.meteringPoint(x,y,jpegOrientation());focusedFrameTimestamp=-1;CompletableFuture<Integer> result=new CompletableFuture<>();
  cameraHandler.post(()->{try{
   repeating.set(CaptureRequest.CONTROL_AF_TRIGGER,CaptureRequest.CONTROL_AF_TRIGGER_CANCEL);session.capture(repeating.build(),results,cameraHandler);focusPoint=point;settings(repeating);pendingFocusTag=UUID.randomUUID().toString();repeating.setTag(pendingFocusTag);pendingFocus=result;
   repeating.set(CaptureRequest.CONTROL_AF_TRIGGER,CaptureRequest.CONTROL_AF_TRIGGER_START);session.capture(repeating.build(),results,cameraHandler);
   repeating.set(CaptureRequest.CONTROL_AF_TRIGGER,CaptureRequest.CONTROL_AF_TRIGGER_IDLE);session.setRepeatingRequest(repeating.build(),results,cameraHandler);
  }catch(Exception e){pendingFocus=null;result.completeExceptionally(e);}});
  try{return result.get(5,TimeUnit.SECONDS)==CaptureResult.CONTROL_AF_STATE_FOCUSED_LOCKED;}catch(TimeoutException e){return false;}finally{if(pendingFocus==result)pendingFocus=null;pendingFocusTag=null;}
 }
 private byte[] capture()throws Exception{
  CompletableFuture<byte[]> result=new CompletableFuture<>();cameraHandler.post(()->{try{if(pendingPhoto!=null)throw new IllegalStateException("相机正忙");pendingPhoto=result;pendingPhotoTimestamp=-1;CaptureRequest.Builder b=camera.createCaptureRequest(CameraDevice.TEMPLATE_STILL_CAPTURE);b.addTarget(jpeg.getSurface());settings(b);int rotation=getWindowManager().getDefaultDisplay().getRotation(),orientation=characteristics.get(CameraCharacteristics.SENSOR_ORIENTATION);int[] rotations={90,0,270,180};b.set(CaptureRequest.JPEG_ORIENTATION,(rotations[rotation]+orientation+270)%360);b.set(CaptureRequest.JPEG_QUALITY,(byte)95);session.capture(b.build(),new CameraCaptureSession.CaptureCallback(){@Override public void onCaptureStarted(CameraCaptureSession s,CaptureRequest r,long timestamp,long frameNumber){if(pendingPhoto==result)pendingPhotoTimestamp=timestamp;}@Override public void onCaptureFailed(CameraCaptureSession s,CaptureRequest r,CaptureFailure f){if(pendingPhoto==result)pendingPhoto=null;result.completeExceptionally(new IllegalStateException("拍照失败"));}},cameraHandler);}catch(Exception e){pendingPhoto=null;result.completeExceptionally(e);}});try{return result.get(15,TimeUnit.SECONDS);}finally{if(pendingPhoto==result)pendingPhoto=null;}
 }
 private void serve(){
  try{
   server=new LocalServerSocket("cardsp-camera-v1");
   while(!stopped){
    try(LocalSocket client=server.accept()){
     client.setSoTimeout(20000);
     BufferedReader in=new BufferedReader(new InputStreamReader(client.getInputStream(),StandardCharsets.UTF_8));
     JSONObject envelope=new JSONObject(boundedLine(in));String id=ZoomPolicy.requestId(envelope.optString("id")),secret=token;
     JSONObject response=new JSONObject();
     try{
      String payload=envelope.getString("payload");
      if(secret.length()<32||!BridgeAuth.verify(secret,"request",id,payload,envelope.optString("auth")))throw new SecurityException("USB服务身份认证失败");
      synchronized(recentIds){if(!recentIds.add(id))throw new SecurityException("重复请求已拒绝");if(recentIds.size()>128)recentIds.remove(recentIds.iterator().next());}
      JSONObject request=new JSONObject(payload);String action=request.getString("action");JSONObject body=state();
      if(action.equals("zoom")){zoom(request.getDouble("zoom"));body=state();}
      if(action.equals("focus")){JSONObject point=request.getJSONObject("point");boolean confirmed=focus(point.getDouble("x"),point.getDouble("y"));body=state().put("focusConfirmed",confirmed);}
      if(action.equals("capture"))body.put("image",android.util.Base64.encodeToString(capture(),android.util.Base64.NO_WRAP)).put("photoTimestamp",Long.toString(lastPhotoTimestamp));
      else if(action.equals("preview")||action.equals("zoom")||action.equals("focus"))body.put("image",android.util.Base64.encodeToString(preview(),android.util.Base64.NO_WRAP)).put("width",1080).put("height",810).put("focusTimestamp",Long.toString(focusedFrameTimestamp)).put("previewTimestamp",Long.toString(lastPreviewTimestamp));
      else if(!action.equals("state"))throw new IllegalArgumentException("未知相机操作");
      Iterator<String> keys=body.keys();while(keys.hasNext()){String key=keys.next();response.put(key,body.get(key));}response.put("ok",true);
     }catch(Exception e){response.put("ok",false).put("error",e instanceof SecurityException?e.getMessage():"相机操作失败，请保持组件在前台并授予相机权限");}
     String payload=response.toString();JSONObject signed=new JSONObject().put("id",id).put("payload",payload).put("auth",BridgeAuth.sign(secret,"response",id,payload));
     client.getOutputStream().write((signed.toString()+"\n").getBytes(StandardCharsets.UTF_8));client.getOutputStream().flush();
    }catch(Exception ignored){}
   }
  }catch(IOException e){message("USB控制通道启动失败，请重开组件");}
 }
 private static String boundedLine(BufferedReader in)throws IOException{StringBuilder s=new StringBuilder();int c;while((c=in.read())!=-1&&c!='\n'){if(s.length()>=2048)throw new IOException("request too long");s.append((char)c);}return s.toString();}
}
