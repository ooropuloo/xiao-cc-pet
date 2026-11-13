' Run a command line with no console window (keeps focus where it is).
' Usage: wscript run-hidden.vbs "<command line>"
Set sh = CreateObject("WScript.Shell")
sh.Run WScript.Arguments(0), 0, False
