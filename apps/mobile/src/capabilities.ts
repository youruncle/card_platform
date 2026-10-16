import { ApiError, type MobileApi } from "./api";
import {
  LEGACY_CAPABILITIES,
  type ClientCapabilities,
} from "../../../packages/card-core/src/capture";
export async function loadCapabilities(
  api: MobileApi,
): Promise<ClientCapabilities> {
  try {
    return {
      ...LEGACY_CAPABILITIES,
      ...(await api.get<ClientCapabilities>("/v1/client-capabilities")),
    };
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return LEGACY_CAPABILITIES;
    throw e;
  }
}
