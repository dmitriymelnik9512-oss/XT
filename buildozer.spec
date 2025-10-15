[app]
title = XT-ScalperPro
package.name = xtscalper
package.domain = org.xt.scalper

source.dir = .
source.include_exts = py,png,jpg,kv,txt,md

version = 1.0

icon.filename = %(source.dir)s/assets/icon.png
presplash.filename = %(source.dir)s/assets/presplash.png

requirements = python3,kivy==2.2.1,ccxt,pandas,numpy,pandas_ta,cython

p4a.bootstrap = sdl2
android.archs = arm64-v8a
orientation = portrait
android.api = 33
android.minapi = 28
android.ndk = 25b
android.permissions = INTERNET,ACCESS_NETWORK_STATE,WAKE_LOCK
android.enable_androidx = True
android.allow_backup = True
android.hardware = touchscreen
android.unicode = True

fullscreen = 0
log_level = 2

[buildozer]