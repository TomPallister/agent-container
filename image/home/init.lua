-- Small offline defaults. Replace this file to use your own Neovim setup.
vim.g.mapleader = " "
vim.opt.number = true
vim.opt.relativenumber = true
vim.opt.expandtab = true
vim.opt.shiftwidth = 2
vim.opt.tabstop = 2
vim.opt.termguicolors = true
vim.opt.undofile = true
vim.keymap.set("n", "<leader>w", "<cmd>write<cr>")
vim.keymap.set("n", "<leader>e", "<cmd>Explore<cr>")
