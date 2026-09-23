' Runs Yap with no console window (tray icon only). Put a shortcut to this in shell:startup to run at login.
Set sh = CreateObject("WScript.Shell")
dir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir
sh.Run """" & dir & "\.venv\Scripts\pythonw.exe"" -m yap --hidden", 0, False
