// Command glowctl installs and operates the GLOW Docker Compose stack.
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"

	"github.com/oxwrc/glow/glowctl/internal/install"
)

const usage = "usage: glowctl <install|status|doctor|users|version> [--dir DIR] [args...]"

var commands = map[string]func(args []string) int{
	"install": installCmd,
	"status":  notImplemented("status"),
	"doctor":  notImplemented("doctor"),
	"users":   notImplemented("users"),
	"version": func([]string) int { fmt.Println("glowctl dev"); return 0 },
}

func notImplemented(name string) func([]string) int {
	return func([]string) int {
		fmt.Fprintf(os.Stderr, "[glowctl] %s: not implemented yet\n", name)
		return 1
	}
}

// defaultDir is the binary's directory when it holds compose.yml, else the cwd.
func defaultDir() string {
	if exe, err := os.Executable(); err == nil {
		d := filepath.Dir(exe)
		if _, err := os.Stat(filepath.Join(d, "compose.yml")); err == nil {
			return d
		}
	}
	d, _ := os.Getwd()
	return d
}

func installCmd(args []string) int {
	fs := flag.NewFlagSet("install", flag.ContinueOnError)
	dir := fs.String("dir", defaultDir(), "release directory")
	admin := fs.String("admin-email", "", "first GLOW admin (production; prompted on a TTY)")
	bundleDir := fs.String("bundle", "", "offline bundle directory (images loaded instead of pulled)")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	rel, err := filepath.Abs(*dir)
	if err != nil {
		fmt.Fprintf(os.Stderr, "[glowctl] install: %v\n", err)
		return 1
	}
	fi, _ := os.Stdin.Stat()
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	err = install.Run(ctx, install.Options{
		ReleaseDir: rel, AdminEmail: *admin, Bundle: *bundleDir,
		Stdin: os.Stdin, IsTTY: fi != nil && fi.Mode()&os.ModeCharDevice != 0, Out: os.Stdout,
	})
	if err != nil {
		fmt.Fprintf(os.Stderr, "[glowctl] install failed: %v\n", err)
		return 1
	}
	return 0
}

func run(args []string) int {
	if len(args) > 0 {
		if cmd, ok := commands[args[0]]; ok {
			return cmd(args[1:])
		}
	}
	fmt.Fprintln(os.Stderr, usage)
	return 2
}

func main() { os.Exit(run(os.Args[1:])) }
