@echo off
setlocal
cd /d "%~dp0"
if not exist native_test mkdir native_test
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
if errorlevel 1 exit /b 1
cl /nologo /LD /O2 /MT /EHsc /std:c++17 /utf-8 /DLSN_API_EXPORTS /I ..\native\include ..\native\bridge.cpp fake_sdk.cpp /link /OUT:native_test\board_camera_bridge.dll
exit /b %errorlevel%
