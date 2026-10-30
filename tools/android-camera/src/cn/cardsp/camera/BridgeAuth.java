package cn.cardsp.camera;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
public final class BridgeAuth {
 public static String sign(String secret,String domain,String id,String payload)throws Exception {
  Mac mac=Mac.getInstance("HmacSHA256");mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8),"HmacSHA256"));
  byte[] bytes=mac.doFinal((domain+"\n"+id+"\n"+payload).getBytes(StandardCharsets.UTF_8));StringBuilder hex=new StringBuilder();for(byte b:bytes)hex.append(String.format(java.util.Locale.ROOT,"%02x",b&255));return hex.toString();
 }
 public static boolean verify(String secret,String domain,String id,String payload,String supplied)throws Exception {
  return supplied!=null&&MessageDigest.isEqual(sign(secret,domain,id,payload).getBytes(StandardCharsets.US_ASCII),supplied.getBytes(StandardCharsets.US_ASCII));
 }
 private BridgeAuth(){}
}
