# Wrapper: py build/build_exes.py — outputs dist/UEFN-Ducky.exe
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
py build/build_exes.py
