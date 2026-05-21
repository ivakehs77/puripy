Output demo_tui.gif

Set Shell "zsh"
Set FontSize 13
Set Width 1100
Set Height 600
Set Theme "Dracula"
Set Padding 20
Set PlaybackSpeed 1.0
Set TypingSpeed 50ms
Set CursorBlink false

Hide
  Type `cd /Users/abishekpuri/Downloads/puripy && source .venv/bin/activate && export PS1='$ '`
  Enter
  Sleep 1s
Show

Ctrl+l
Sleep 200ms

Type "puripy replay demo.trace --tui"
Enter
Sleep 2s

# Step through frames into main body
Sleep 400ms
Type "j"
Sleep 350ms
Type "j"
Sleep 350ms
Type "j"
Sleep 350ms
Type "j"
Sleep 350ms
Type "j"
Sleep 350ms
Type "j"
Sleep 350ms
Type "j"
Sleep 350ms
Type "j"
Sleep 350ms
Type "j"
Sleep 350ms
Type "j"
Sleep 350ms
Type "j"
Sleep 350ms
Type "j"
Sleep 500ms

# Step over a few function calls
Type "n"
Sleep 600ms
Type "n"
Sleep 600ms
Type "n"
Sleep 600ms
Type "n"
Sleep 700ms

# Step back
Type "k"
Sleep 500ms
Type "k"
Sleep 500ms

# Open AI panel
Type "?"
Sleep 1000ms

# Quit
Type "q"
Sleep 500ms
