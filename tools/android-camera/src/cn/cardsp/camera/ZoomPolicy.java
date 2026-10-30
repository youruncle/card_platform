package cn.cardsp.camera;
public final class ZoomPolicy {
 public static float validate(double value,float min,float max) {
  if(!Double.isFinite(value)||value<min||value>max)throw new IllegalArgumentException("倍率超出镜头范围");return (float)value;
 }
 public static boolean same(double actual,double requested) { return Double.isFinite(actual)&&Math.abs(actual-requested)<0.025; }
 public static float[] meteringPoint(double x,double y,int orientation) {
  if(!Double.isFinite(x)||!Double.isFinite(y)||x<0||y<0||x>1||y>1)throw new IllegalArgumentException("对焦位置无效");
  switch(orientation){case 0:return new float[]{(float)x,(float)y};case 90:return new float[]{(float)y,(float)(1-x)};case 180:return new float[]{(float)(1-x),(float)(1-y)};case 270:return new float[]{(float)(1-y),(float)x};default:throw new IllegalArgumentException("相机方向无效");}
 }
 public static double cameraScore(boolean rear,float focus,float width,float height) { if(!rear||!Float.isFinite(width)||!Float.isFinite(height)||width<=0||height<=0)return -1;return (focus>0?1000000:0)+(double)width*height; }
 public static boolean previewReady(long matched,long displayed) { return matched>0&&displayed>=matched; }
 public static String requestId(String value) {
  if(value==null||!value.matches("[A-Za-z0-9_-]{16,80}"))throw new IllegalArgumentException("请求标识无效");return value;
 }
 private ZoomPolicy(){}
}
