param(
  [string]$Name = "LDPlayerMultiTool"
)
python -m pip install -r requirements.txt
python -m pip install pyinstaller
pyinstaller --noconfirm --windowed --name $Name --add-data "configs;configs" --add-data "resources;resources" app.py
Write-Host "Build output: dist/$Name"
