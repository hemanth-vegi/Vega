' Starts Vega 2.0 (vega_webview.py) without a black command window.
' Only one Vega can run: if she's already running, her window just comes to the front.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = folder
py = "python"
If fso.FileExists(folder & "\python_path.txt") Then
  Set f = fso.OpenTextFile(folder & "\python_path.txt", 1)
  If Not f.AtEndOfStream Then p = Trim(f.ReadLine)
  f.Close
  If p <> "" And fso.FileExists(p) Then py = """" & p & """"
End If
sh.Run py & " """ & folder & "\vega_webview.py""", 0, False
