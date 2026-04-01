# Shell Tab Completion

ContextGO supports tab completion for bash, zsh, and fish.

## Setup

### bash

```bash
echo 'eval "$(contextgo completion bash)"' >> ~/.bashrc
source ~/.bashrc
```

### zsh

```bash
echo 'eval "$(contextgo completion zsh)"' >> ~/.zshrc
source ~/.zshrc
```

### fish

```bash
echo 'contextgo completion fish | source' >> ~/.config/fish/config.fish
```

## Manual Inspection

Print the completion script without installing it:

```bash
contextgo completion bash
contextgo completion zsh
contextgo completion fish
```
