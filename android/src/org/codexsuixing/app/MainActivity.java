package org.codexsuixing.app;

import android.app.Activity;
import android.app.KeyguardManager;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.hardware.biometrics.BiometricPrompt;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Build;
import android.os.Bundle;
import android.os.CancellationSignal;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.SslErrorHandler;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.ValueCallback;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import org.json.JSONObject;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import javax.net.ssl.HttpsURLConnection;

public final class MainActivity extends Activity {
    private static final String BASE = ServerConfig.BASE_URL;
    private static final String KEY = "codex-suixing-device-v1";
    private static final int CREDENTIAL_REQUEST = 40;
    private static final int IMAGE_REQUEST = 41;
    private ValueCallback<Uri[]> imageCallback;
    private boolean pickingImages;
    private static final int BG = 0xfff4f5f1, INK = 0xff202b28, GREEN = 0xff256c50;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private WebView web;
    private LinearLayout gate;
    private TextView explanation;
    private Button unlockButton, bindButton;
    private ProgressBar progress;
    private SharedPreferences prefs;
    private String pendingCode = "";
    private boolean unlocked, authenticating, busy, foreground, loadFailed;
    private CancellationSignal biometricCancellation;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        prefs = getSharedPreferences("device", MODE_PRIVATE);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        buildUi();
        receiveActivation(getIntent());
        if (Build.VERSION.SDK_INT >= 33) getOnBackInvokedDispatcher().registerOnBackInvokedCallback(0, this::goBack);
        showGate(ready() ? "使用指纹或系统锁屏密码解锁。" : "首次在已登录网页中绑定这台手机，之后免输网页密码。", false);
    }

    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    private TextView text(String value, int size) { TextView v = new TextView(this); v.setText(value); v.setTextSize(size); v.setTextColor(INK); return v; }
    private Button button(String value) { Button b = new Button(this); b.setText(value); b.setAllCaps(false); b.setTextColor(GREEN); return b; }
    private void buildUi() {
        LinearLayout root = new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL); root.setBackgroundColor(BG);
        root.setOnApplyWindowInsetsListener((v, insets) -> {
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout() | WindowInsets.Type.ime());
                v.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            } else v.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(), insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            return insets.consumeSystemWindowInsets();
        });
        LinearLayout toolbar = new LinearLayout(this); toolbar.setGravity(Gravity.CENTER_VERTICAL); toolbar.setPadding(dp(12), 0, dp(8), 0);
        TextView title = text("Codex 随行", 15); toolbar.addView(title, new LinearLayout.LayoutParams(0, dp(44), 1)); title.setGravity(Gravity.CENTER_VERTICAL);
        Button refresh = button("刷新"); refresh.setContentDescription("刷新对话"); refresh.setOnClickListener(v -> { if (unlocked) web.reload(); else unlock(); });
        toolbar.addView(refresh, new LinearLayout.LayoutParams(dp(72), dp(44))); root.addView(toolbar);
        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal); progress.setMax(100); progress.setVisibility(View.GONE); root.addView(progress, new LinearLayout.LayoutParams(-1, dp(2)));
        FrameLayout frame = new FrameLayout(this); root.addView(frame, new LinearLayout.LayoutParams(-1, 0, 1));
        web = new WebView(this); frame.addView(web, new FrameLayout.LayoutParams(-1, -1)); web.setBackgroundColor(BG); web.setVisibility(View.INVISIBLE);
        WebSettings settings = web.getSettings(); settings.setJavaScriptEnabled(true); settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false); settings.setAllowContentAccess(true); settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setJavaScriptCanOpenWindowsAutomatically(false); settings.setSupportMultipleWindows(false); settings.setSaveFormData(false);
        settings.setUserAgentString(settings.getUserAgentString() + " CodexSuixing/1.2.0");
        CookieManager.getInstance().setAcceptCookie(true); CookieManager.getInstance().setAcceptThirdPartyCookies(web, false);
        web.setWebChromeClient(new WebChromeClient() {
            @Override public void onProgressChanged(WebView v, int percent) { progress.setProgress(percent); if (percent == 100 && !busy) progress.setVisibility(View.GONE); }
            @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (!unlocked || !trusted(Uri.parse(view.getUrl() == null ? "" : view.getUrl()))) return false;
                if (imageCallback != null) imageCallback.onReceiveValue(null);
                imageCallback = callback;
                Intent pick = new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("image/*");
                pick.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, params.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE);
                pick.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                try { pickingImages = true; startActivityForResult(pick, IMAGE_REQUEST); }
                catch (Exception error) { pickingImages = false; imageCallback = null; callback.onReceiveValue(null); }
                return true;
            }
        });
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                if (trusted(request.getUrl())) return false;
                if (request.isForMainFrame() && request.hasGesture()) openExternal(request.getUrl());
                return true;
            }
            @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                if (trusted(request.getUrl())) return null;
                return new WebResourceResponse("text/plain", "utf-8", 403, "Blocked", java.util.Collections.emptyMap(), new ByteArrayInputStream(new byte[0]));
            }
            @Override public void onPageStarted(WebView view, String url, android.graphics.Bitmap icon) { loadFailed = false; progress.setVisibility(View.VISIBLE); }
            @Override public void onPageFinished(WebView view, String url) {
                progress.setVisibility(View.GONE); CookieManager.getInstance().flush();
                if (unlocked && !loadFailed) { gate.setVisibility(View.GONE); web.setVisibility(View.VISIBLE); }
            }
            @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) { loadFailed = true; unlocked = false; showGate("暂时无法连接服务器。请检查手机网络后重试。", true); }
            }
            @Override public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse response) {
                if (response.getStatusCode() == 401 && unlocked && trusted(request.getUrl())) runOnUiThread(() -> { unlocked = false; showGate("登录会话已更新，请解锁重新连接，无需输入网页密码。", true); });
            }
            @Override public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
                handler.cancel(); loadFailed = true; unlocked = false;
                showGate("服务器证书无法验证。请检查手机日期；若服务器更换过证书，需要更新 App。", true);
            }
        });
        gate = new LinearLayout(this); gate.setOrientation(LinearLayout.VERTICAL); gate.setGravity(Gravity.CENTER); gate.setPadding(dp(30), dp(24), dp(30), dp(24)); gate.setBackgroundColor(BG);
        TextView mark = text("‹ / ›", 45); mark.setTextColor(GREEN); gate.addView(mark);
        TextView heading = text("工作，随时继续。", 25); heading.setGravity(Gravity.CENTER); gate.addView(heading);
        explanation = text("", 15); explanation.setTextColor(0xff69756f); explanation.setGravity(Gravity.CENTER); explanation.setPadding(0, dp(20), 0, dp(20)); gate.addView(explanation);
        unlockButton = button("解锁并打开"); unlockButton.setOnClickListener(v -> unlock()); gate.addView(unlockButton, new LinearLayout.LayoutParams(-1, dp(54)));
        bindButton = button("一键绑定手机"); bindButton.setOnClickListener(v -> startBinding()); gate.addView(bindButton, new LinearLayout.LayoutParams(-1, dp(54)));
        frame.addView(gate, new FrameLayout.LayoutParams(-1, -1)); setContentView(root); root.requestApplyInsets();
    }

    private boolean trusted(Uri uri) {
        Uri expected = Uri.parse(BASE);
        return uri != null && "https".equals(uri.getScheme()) && expected.getHost().equalsIgnoreCase(String.valueOf(uri.getHost())) && uri.getPort() == expected.getPort() && uri.getUserInfo() == null;
    }
    private boolean ready() { return prefs.contains("encryptedToken"); }
    private void forgetInvalidKey(Exception error) {
        if (!(error instanceof android.security.keystore.KeyPermanentlyInvalidatedException) && !(error instanceof java.security.UnrecoverableKeyException)) return;
        try { KeyStore store = KeyStore.getInstance("AndroidKeyStore"); store.load(null); store.deleteEntry(KEY); } catch (Exception ignored) { }
        prefs.edit().remove("encryptedToken").remove("tokenIv").apply(); pendingCode = "";
    }
    private void showGate(String message, boolean retry) {
        gate.setVisibility(View.VISIBLE); web.setVisibility(View.INVISIBLE); explanation.setText(message);
        unlockButton.setVisibility(ready() || !pendingCode.isEmpty() ? View.VISIBLE : View.GONE);
        unlockButton.setText(!pendingCode.isEmpty() ? "验证并完成绑定" : retry ? "解锁重试" : "解锁并打开");
        unlockButton.setEnabled(!busy && !authenticating); bindButton.setEnabled(!busy && !authenticating);
        bindButton.setText(ready() ? "重新绑定手机" : "一键绑定手机");
    }
    private static String b64(byte[] bytes) { return Base64.encodeToString(bytes, Base64.URL_SAFE | Base64.NO_PADDING | Base64.NO_WRAP); }
    private void startBinding() {
        try {
            if (!((KeyguardManager)getSystemService(KEYGUARD_SERVICE)).isDeviceSecure()) { showGate("请先在手机系统设置中添加锁屏密码，再绑定 App。", false); return; }
            pendingCode = "";
            byte[] random = new byte[32]; new SecureRandom().nextBytes(random); String verifier = b64(random);
            prefs.edit().putString("pairVerifier", verifier).commit();
            String challenge = b64(MessageDigest.getInstance("SHA-256").digest(verifier.getBytes(StandardCharsets.UTF_8)));
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(BASE + "android#challenge=" + challenge)));
        } catch (Exception e) { showGate("未能打开浏览器，请稍后重试。", false); }
    }
    private void openExternal(Uri uri) {
        String scheme = uri.getScheme();
        if (!"https".equals(scheme) && !"http".equals(scheme) && !"mailto".equals(scheme)) return;
        try { startActivity(new Intent(Intent.ACTION_VIEW, uri)); } catch (Exception ignored) { }
    }
    private void receiveActivation(Intent intent) {
        Uri uri = intent == null ? null : intent.getData();
        if (uri != null && "codexsuixing".equals(uri.getScheme()) && "activate".equals(uri.getHost())) {
            String code = uri.getQueryParameter("code");
            if (code != null && code.matches("[A-Za-z0-9_-]{43}") && prefs.contains("pairVerifier")) pendingCode = code;
            intent.setData(null);
        }
    }
    @Override protected void onNewIntent(Intent intent) { super.onNewIntent(intent); setIntent(intent); receiveActivation(intent); unlocked = false; showGate("请验证身份，完成这台手机的绑定。", false); }

    private SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore"); store.load(null);
        if (store.containsAlias(KEY)) return (SecretKey)store.getKey(KEY, null);
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        KeyGenParameterSpec.Builder options = new KeyGenParameterSpec.Builder(KEY, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).setUserAuthenticationRequired(true);
        if (Build.VERSION.SDK_INT >= 30) options.setUserAuthenticationParameters(60, KeyProperties.AUTH_BIOMETRIC_STRONG | KeyProperties.AUTH_DEVICE_CREDENTIAL);
        else options.setUserAuthenticationValidityDurationSeconds(60);
        generator.init(options.build()); return generator.generateKey();
    }
    private void saveToken(String token) throws Exception {
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding"); cipher.init(Cipher.ENCRYPT_MODE, key());
        byte[] encrypted = cipher.doFinal(token.getBytes(StandardCharsets.UTF_8));
        if (!prefs.edit().putString("encryptedToken", b64(encrypted)).putString("tokenIv", b64(cipher.getIV())).remove("pairVerifier").commit()) throw new Exception("保存绑定失败");
    }
    private String readToken() throws Exception {
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, Base64.decode(prefs.getString("tokenIv", ""), Base64.URL_SAFE)));
        return new String(cipher.doFinal(Base64.decode(prefs.getString("encryptedToken", ""), Base64.URL_SAFE)), StandardCharsets.UTF_8);
    }
    private void unlock() {
        if (authenticating || busy) return;
        if (!ready() && pendingCode.isEmpty()) { startBinding(); return; }
        KeyguardManager lock = (KeyguardManager)getSystemService(KEYGUARD_SERVICE);
        if (!lock.isDeviceSecure()) { showGate("请先在手机系统设置中添加锁屏密码，再使用免密登录。", false); return; }
        try { key(); } catch (Exception error) { forgetInvalidKey(error); showGate("手机安全密钥无法使用，请重新绑定。", false); return; }
        authenticating = true; unlockButton.setEnabled(false); bindButton.setEnabled(false);
        if (Build.VERSION.SDK_INT >= 30) {
            biometricCancellation = new CancellationSignal();
            new BiometricPrompt.Builder(this).setTitle("解锁 Codex 随行").setSubtitle("验证后连接你自己的 Codex")
                .setAllowedAuthenticators(android.hardware.biometrics.BiometricManager.Authenticators.BIOMETRIC_STRONG | android.hardware.biometrics.BiometricManager.Authenticators.DEVICE_CREDENTIAL)
                .build().authenticate(biometricCancellation, getMainExecutor(), new BiometricPrompt.AuthenticationCallback() {
                    @Override public void onAuthenticationSucceeded(BiometricPrompt.AuthenticationResult result) { authenticating = false; connect(); }
                    @Override public void onAuthenticationError(int code, CharSequence message) { authenticating = false; showGate(message.toString(), false); }
                });
        } else {
            Intent confirm = lock.createConfirmDeviceCredentialIntent("解锁 Codex 随行", "验证手机锁屏密码后连接");
            if (confirm != null) startActivityForResult(confirm, CREDENTIAL_REQUEST);
            else { authenticating = false; showGate("请设置手机锁屏密码后重试。", false); }
        }
    }
    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == IMAGE_REQUEST) {
            pickingImages = false;
            ValueCallback<Uri[]> callback = imageCallback; imageCallback = null;
            if (callback != null) {
                java.util.ArrayList<Uri> images = new java.util.ArrayList<>();
                if (resultCode == RESULT_OK && data != null) {
                    java.util.ArrayList<Uri> candidates = new java.util.ArrayList<>();
                    if (data.getClipData() != null && data.getClipData().getItemCount() <= 4) {
                        for (int i = 0; i < data.getClipData().getItemCount(); i++) candidates.add(data.getClipData().getItemAt(i).getUri());
                    } else if (data.getClipData() == null && data.getData() != null) candidates.add(data.getData());
                    for (Uri uri : candidates) {
                        try {
                            String mime = getContentResolver().getType(uri);
                            if ("content".equals(uri.getScheme()) && mime != null && mime.startsWith("image/")) images.add(uri);
                        } catch (Exception ignored) { }
                    }
                }
                callback.onReceiveValue(images.isEmpty() ? null : images.toArray(new Uri[0]));
            }
        }
        if (requestCode == CREDENTIAL_REQUEST) { authenticating = false; if (resultCode == RESULT_OK) connect(); else showGate("解锁已取消。", false); }
    }

    private static final class Reply { JSONObject json; String cookie; }
    private Reply request(String endpoint, JSONObject body, String token) throws Exception {
        HttpsURLConnection connection = (HttpsURLConnection)new URL(BASE + endpoint).openConnection();
        connection.setConnectTimeout(12000); connection.setReadTimeout(12000); connection.setInstanceFollowRedirects(false);
        connection.setRequestMethod("POST"); connection.setDoOutput(true); connection.setRequestProperty("Content-Type", "application/json");
        if (token != null) connection.setRequestProperty("Authorization", "Bearer " + token);
        byte[] data = body.toString().getBytes(StandardCharsets.UTF_8); connection.setFixedLengthStreamingMode(data.length);
        try {
            try (java.io.OutputStream out = connection.getOutputStream()) { out.write(data); }
            int status = connection.getResponseCode();
            InputStream source = status == 200 ? connection.getInputStream() : connection.getErrorStream();
            if (source == null) throw new Exception("服务器暂时不可用");
            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            try (InputStream in = source) { byte[] buf = new byte[2048]; int size; while ((size = in.read(buf)) != -1) { bytes.write(buf, 0, size); if (bytes.size() > 16384) throw new Exception("服务器响应异常"); } }
            JSONObject json = new JSONObject(bytes.toString("UTF-8"));
            if (status != 200) throw new Exception(json.optString("error", "连接失败，请重试"));
            Reply reply = new Reply(); reply.json = json; reply.cookie = connection.getHeaderField("Set-Cookie"); return reply;
        } finally { connection.disconnect(); }
    }
    private void connect() {
        busy = true; explanation.setText("正在安全连接…"); progress.setVisibility(View.VISIBLE); unlockButton.setEnabled(false); bindButton.setEnabled(false);
        worker.execute(() -> {
            try {
                String token;
                if (!pendingCode.isEmpty()) {
                    JSONObject body = new JSONObject().put("code", pendingCode).put("verifier", prefs.getString("pairVerifier", "")).put("name", Build.MANUFACTURER + " " + Build.MODEL);
                    Reply activation = request("api/android/activate", body, null); token = activation.json.getString("token"); saveToken(token); pendingCode = "";
                } else token = readToken();
                Reply session = request("api/android/session", new JSONObject(), token);
                if (session.cookie == null || !session.cookie.startsWith("viewer_session=")) throw new Exception("登录会话无效");
                runOnUiThread(() -> {
                    busy = false; progress.setVisibility(View.GONE);
                    CookieManager.getInstance().setCookie(BASE, session.cookie, accepted -> {
                        CookieManager.getInstance().flush();
                        if (Boolean.TRUE.equals(accepted) && foreground) { unlocked = true; gate.setVisibility(View.GONE); web.setVisibility(View.VISIBLE); web.loadUrl(BASE); }
                        else showGate("手机已绑定，请解锁打开对话。", false);
                    });
                });
            } catch (Exception error) {
                runOnUiThread(() -> {
                    busy = false; progress.setVisibility(View.GONE); unlocked = false;
                    forgetInvalidKey(error);
                    String message;
                    if (error instanceof javax.net.ssl.SSLException) message = "服务器证书无法验证，请检查手机日期或更新 App。";
                    else if (error instanceof java.io.IOException) message = "暂时无法连接服务器，请检查手机网络后重试。";
                    else if (error instanceof java.security.GeneralSecurityException) message = "手机密钥已锁定或失效，请重试解锁；仍失败时重新绑定。";
                    else message = error.getMessage() == null ? "连接失败，请重试。" : error.getMessage();
                    showGate(message, true);
                });
            }
        });
    }
    private void goBack() {
        if (!unlocked || !trusted(Uri.parse(web.getUrl() == null ? BASE : web.getUrl()))) { finish(); return; }
        web.evaluateJavascript("(function(){var d=document.querySelector('dialog[open]');if(d){d.close();return true;}var s=document.getElementById('shell');if(s&&s.classList.contains('open')&&innerWidth<=720){s.classList.remove('open');return true;}return false;})()", handled -> { if (!"true".equals(handled)) { if (web.canGoBack()) web.goBack(); else finish(); } });
    }
    @Override public void onBackPressed() { goBack(); }
    @Override protected void onResume() { super.onResume(); foreground = true; web.onResume(); web.resumeTimers(); if (!unlocked && !authenticating && !busy) showGate(!pendingCode.isEmpty() ? "请验证身份，完成手机绑定。" : ready() ? "使用指纹或系统锁屏密码解锁。" : "首次在已登录网页中绑定这台手机，之后免输网页密码。", false); }
    @Override protected void onPause() { CookieManager.getInstance().flush(); web.onPause(); super.onPause(); }
    @Override protected void onStop() { super.onStop(); foreground = false; web.pauseTimers(); if (!pickingImages) { unlocked = false; if (!authenticating) showGate("对话已锁定。", false); } }
    @Override protected void onDestroy() { if (imageCallback != null) { imageCallback.onReceiveValue(null); imageCallback = null; } if (biometricCancellation != null) biometricCancellation.cancel(); web.destroy(); worker.shutdownNow(); super.onDestroy(); }
}
