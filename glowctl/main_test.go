package main

import "testing"

func TestRunDispatch(t *testing.T) {
	for args, want := range map[string]int{"": 2, "bogus": 2, "version": 0, "install": 1} {
		var a []string
		if args != "" {
			a = []string{args}
		}
		if got := run(a); got != want {
			t.Errorf("run(%q)=%d want %d", args, got, want)
		}
	}
}
