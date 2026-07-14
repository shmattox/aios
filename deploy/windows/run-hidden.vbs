Set sh = CreateObject("WScript.Shell")
args = ""
For i = 0 To WScript.Arguments.Count - 1
  args = args & " """ & WScript.Arguments(i) & """"
Next
rc = sh.Run("powershell -NoProfile -ExecutionPolicy Bypass -File" & args, 0, True)
WScript.Quit rc
