# oi — a fast, multi-provider LLM chat client for your terminal
# Render with:  vhs demo.tape   (writes demo.gif)

Output demo.gif

Set Theme "Catppuccin Mocha"
Set FontSize 16
Set Width 1200
Set Height 700
Set Padding 20
Set TypingSpeed 70ms

Sleep 1s

# Start a chat with the concise prompt (default model)
Type "oi -P concise"
Enter
Sleep 2s

# Multi-turn: the chat keeps context across every turn
Type "What is quantum entanglement?"
Enter
Sleep 7s

Type "Now explain it like I'm five."
Enter
Sleep 7s

Type "So could I use it to send messages faster than light?"
Enter
Sleep 8s

Ctrl+C
Sleep 1s

# Different provider, same interface — headless one-shot, pipe-friendly
Type "oi -m gpt -p 'explain entropy in one tweet' --no-thinking"
Enter
Sleep 8s
