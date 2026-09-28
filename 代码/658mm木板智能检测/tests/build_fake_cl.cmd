@echo off
setlocal
cd /d "%~dp0"
if not exist native_cl mkdir native_cl
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
if errorlevel 1 exit /b 1
cl /nologo /LD /O2 /MT /EHsc /std:c++17 /utf-8 /DLSNCLC_EXPORT /I ..\native\include ..\native\cl_config_bridge.cpp fake_cl_config.cpp /link /OUT:native_cl\cl_config_bridge.dll
exit /b %errorlevel%
