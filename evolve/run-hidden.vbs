' Run one evolve job hidden (no console window): wscript run-hidden.vbs survey|ask
Set sh = CreateObject("WScript.Shell")
here = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
job = WScript.Arguments(0)
sh.CurrentDirectory = here
sh.Environment("Process")("PYTHONIOENCODING") = "utf-8"
q = Chr(34)
cmd = "cmd /c " & q & q & "C:\Python39\python.exe" & q & " -u " & q & here & "\" & job & ".py" & q & _
      " >> " & q & here & "\logs\" & job & ".out" & q & " 2>&1" & q
sh.Run cmd, 0, True
