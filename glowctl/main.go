// Command glowctl installs and operates the GLOW Docker Compose stack.
package main

import (
	"fmt"
	"os"
)

const usage = "usage: glowctl <install|status|doctor|users|version> [--dir DIR] [args...]"

var commands = map[string]func(args []string) int{
	"install": notImplemented("install"),
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
