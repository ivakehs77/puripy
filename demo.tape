Output demo.gif

Set Shell "zsh"
Set FontSize 14
Set Width 900
Set Height 480
Set Theme "Dracula"
Set Padding 20
Set PlaybackSpeed 1.25
Set TypingSpeed 45ms
Set CursorBlink false

Hide
  Type `cd /Users/abishekpuri/Downloads/puripy && source .venv/bin/activate && export PS1='$ '`
  Enter
  Sleep 1s
Show

Ctrl+l
Sleep 300ms

Type "puripy record examples/demo.py"
Enter
Sleep 2s

Type "puripy replay demo.trace"
Enter
Sleep 1500ms

Type "goto line 40"
Enter
Sleep 900ms

Type "step"
Enter
Sleep 900ms

Type "step"
Enter
Sleep 900ms

Type "show percentage"
Enter
Sleep 900ms

Type "out"
Enter
Sleep 800ms

Type "find grade F"
Enter
Sleep 800ms

Type "quit"
Enter
Sleep 500ms
