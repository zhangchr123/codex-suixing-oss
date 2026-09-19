"""Build/sign a small dependency-free APK with Android SDK 35 and Java 17.

Use a standard Android SDK; generated files and signing keys stay in ../.state/.
No passwords or device tokens are bundled in the APK.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import zipfile
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATE = ROOT / ".state"
SDK = Path(os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or STATE / "android-sdk")
EXE = ".exe" if os.name == "nt" else ""
BUILD = STATE / "android-build"
SIGNING = STATE / "android-signing"
APK = ROOT / "downloads" / "codex-suixing.apk"


def run(*args, **kwargs):
    subprocess.run([str(x) for x in args], check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection", type=Path, default=STATE / "connection.json")
    parser.add_argument("--certificate", type=Path, help="Optional public certificate for a self-signed HTTPS server; otherwise use system CAs")
    parser.add_argument("--output", type=Path, default=APK)
    parser.add_argument("--version-code", type=int, default=1)
    parser.add_argument("--version-name", default="1.0.0")
    parser.add_argument("--sdk", type=Path, default=SDK)
    parser.add_argument("--build-tools", default="35.0.0")
    args = parser.parse_args()
    tools = args.sdk / "build-tools" / args.build_tools
    platform = args.sdk / "platforms/android-35/android.jar"
    output = args.output.resolve()
    for name in ("java", "javac", "keytool"):
        if not shutil.which(name):
            raise SystemExit(f"Java 17 required: {name} not found")
    if not platform.is_file() or not (tools / ("aapt2" + EXE)).is_file():
        raise SystemExit("Install Android platform 35 and the selected build-tools version in --sdk or ANDROID_HOME")
    connection = json.loads(args.connection.read_text(encoding="utf-8"))
    server = urlsplit(connection["url"])
    if server.scheme != "https" or not server.hostname or server.username or server.password or server.path not in ("", "/") or server.query or server.fragment:
        raise SystemExit("The server URL must be a plain HTTPS origin without credentials")
    for folder in (BUILD, SIGNING, output.parent):
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Remove only generated output under this verified build directory.
    for name in ("classes", "dex", "generated", "res"):
        folder = (BUILD / name).resolve()
        if not folder.is_relative_to(BUILD.resolve()):
            raise SystemExit("Unsafe build path")
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir()
    generated = BUILD / "generated/org/codexsuixing/app/ServerConfig.java"
    generated.parent.mkdir(parents=True, exist_ok=True)
    generated.write_text('package org.codexsuixing.app; final class ServerConfig { static final String BASE_URL = ' + json.dumps(connection["url"].rstrip("/")+"/") + '; }\n', encoding="utf-8")
    namespace = "http://schemas.android.com/apk/res/android"
    ET.register_namespace("android", namespace)
    manifest = ET.parse(HERE / "AndroidManifest.xml")
    manifest.getroot().set("{"+namespace+"}versionCode", str(args.version_code))
    manifest.getroot().set("{"+namespace+"}versionName", args.version_name)
    manifest.write(BUILD / "AndroidManifest.xml", encoding="utf-8", xml_declaration=True)
    shutil.copytree(HERE / "res", BUILD / "res", dirs_exist_ok=True)
    if args.certificate:
        certificate = args.certificate.read_bytes()
        if b"PRIVATE KEY" in certificate or b"BEGIN CERTIFICATE" not in certificate:
            raise SystemExit("--certificate must contain only public PEM certificates, never a private key")
        (BUILD / "res/raw").mkdir(exist_ok=True)
        (BUILD / "res/raw/server_certificate.pem").write_bytes(certificate)
        security = BUILD / "res/xml/network_security_config.xml"
        security.write_text(security.read_text().replace('src="system"', 'src="@raw/server_certificate"'), encoding="utf-8")
    # Configured public certificates go into generated resources, never source files.
    run(tools / ("aapt2" + EXE), "compile", "--dir", BUILD / "res", "-o", BUILD / "resources.zip")
    run(tools / ("aapt2" + EXE), "link", "-o", BUILD / "resources.apk", "-I", platform,
        "--manifest", BUILD / "AndroidManifest.xml", "--java", BUILD / "generated", BUILD / "resources.zip")
    sources = list((HERE / "src").rglob("*.java")) + list((BUILD / "generated").rglob("*.java"))
    run("javac", "-encoding", "UTF-8", "-source", "8", "-target", "8", "-Xlint:-options", "-classpath", platform, "-d", BUILD / "classes", *sources)
    run("java", "-cp", tools / "lib/d8.jar", "com.android.tools.r8.D8", "--release", "--min-api", "28", "--lib", platform,
        "--output", BUILD / "dex", *list((BUILD / "classes").rglob("*.class")))
    unsigned = BUILD / "unsigned.apk"
    shutil.copyfile(BUILD / "resources.apk", unsigned)
    with zipfile.ZipFile(unsigned, "a", compression=zipfile.ZIP_DEFLATED) as out:
        for dex in (BUILD / "dex").glob("*.dex"):
            out.write(dex, dex.name)
    run(tools / ("zipalign" + EXE), "-f", "-p", "4", unsigned, BUILD / "aligned.apk")
    password_file, keystore = SIGNING / "password.txt", SIGNING / "release.p12"
    if not password_file.exists():
        if keystore.exists():
            raise SystemExit("Existing signing key requires its original password; refusing to replace it")
        password_file.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        password_file.chmod(0o600)
    if not keystore.exists():
        run("keytool", "-genkeypair", "-keystore", keystore, "-storetype", "PKCS12", "-alias", "codex-suixing", "-keyalg", "RSA", "-keysize", "3072",
            "-validity", "10000", "-dname", "CN=Codex Suixing Personal App", "-storepass:file", password_file, "-keypass:file", password_file)
    run("java", "-jar", tools / "lib/apksigner.jar", "sign", "--ks", keystore, "--ks-key-alias", "codex-suixing", "--ks-pass", "file:"+str(password_file),
        "--v4-signing-enabled", "false", "--out", output, BUILD / "aligned.apk")
    run("java", "-jar", tools / "lib/apksigner.jar", "verify", "--verbose", "--print-certs", output)
    run(tools / ("zipalign" + EXE), "-c", "4", output)
    sha = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".apk.sha256").write_text(sha + "  " + output.name + "\n", encoding="ascii")
    keystore.chmod(0o600)
    print(f"APK: {output}\nBytes: {output.stat().st_size}\nSHA256: {sha}")


if __name__ == "__main__":
    main()
