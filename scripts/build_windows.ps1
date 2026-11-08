# Build Outly.exe with PyInstaller and place it in release/.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

python -m pip install -r requirements-dev.txt
New-Item -ItemType Directory -Force build | Out-Null
Copy-Item assets/logo.ico build/outly.ico -Force
@'
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(1, 0, 0, 0),
    prodvers=(1, 0, 0, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'Outly'),
        StringStruct('FileDescription', 'Outly - local YouTube competitor intelligence'),
        StringStruct('FileVersion', '1.0.0'),
        StringStruct('InternalName', 'Outly'),
        StringStruct('OriginalFilename', 'Outly.exe'),
        StringStruct('ProductName', 'Outly'),
        StringStruct('ProductVersion', '1.0.0')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
'@ | Set-Content -Path build/version-outly.txt -Encoding ascii

python -m PyInstaller --noconfirm --clean --log-level WARN Outly.spec
New-Item -ItemType Directory -Force release | Out-Null
Copy-Item dist/Outly.exe release/Outly.exe -Force
$hash = (Get-FileHash release/Outly.exe -Algorithm SHA256).Hash.ToLower()
Write-Host "release/Outly.exe SHA256=$hash"
