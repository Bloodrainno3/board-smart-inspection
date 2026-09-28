@echo off
setlocal
cd /d "%~dp0"
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
if errorlevel 1 exit /b 1
cl /nologo /LD /O2 /MT /EHsc /std:c++17 /utf-8 /I include cl_config_bridge.cpp lib\lsn_cl_configurator.lib /link /OUT:..\camera_driver\cl_config_bridge.dll
exit /b %errorlevel%
