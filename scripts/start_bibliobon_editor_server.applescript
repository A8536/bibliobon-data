set projectDir to "/Users/oleg/Projects/data/bibliobon-data"
set pythonBin to "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3"
set serverHost to "127.0.0.1"
set serverPort to "8001"

set shellCommand to "cd " & quoted form of projectDir & " && " & ¬
	"if /usr/sbin/lsof -nP -iTCP:" & serverPort & " -sTCP:LISTEN >/dev/null 2>&1; then " & ¬
	"echo 'Bibliobon editor server is already running on http://biblio-admin.test:" & serverPort & "/'; " & ¬
	"else " & quoted form of pythonBin & " editor/manage.py runserver " & serverHost & ":" & serverPort & " --noreload; fi"

tell application "Terminal"
	activate
	do script shellCommand
end tell
