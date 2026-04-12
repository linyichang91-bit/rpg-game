# Start backend
Start-Process powershell -ArgumentList "-NoLogo -ExecutionPolicy Bypass -Command cd 'c:\Users\linyi\OneDrive\桌面\rpg-game'; .\.venv\Scripts\python.exe -m uvicorn server.api.app:app --host 127.0.0.1 --port 8000 2>&1 | Tee-Object -FilePath logs\backend.log" -WindowStyle Normal

Start-Sleep -Seconds 2

# Start frontend
Start-Process powershell -ArgumentList "-NoLogo -ExecutionPolicy Bypass -Command cd 'c:\Users\linyi\OneDrive\桌面\rpg-game'; npm run dev" -WindowStyle Normal

Write-Host "Servers starting..."
