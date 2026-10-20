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

func TestSplitDir(t *testing.T) {
	d, rest, err := splitDir([]string{"add", "--dir", "/x", "a@b.org", "--admin"})
	if err != nil || d != "/x" || len(rest) != 3 || rest[0] != "add" || rest[2] != "--admin" {
		t.Fatalf("%q %q %v", d, rest, err)
	}
	if d, _, _ := splitDir([]string{"--dir=/y"}); d != "/y" {
		t.Fatal(d)
	}
	if _, _, err := splitDir([]string{"--dir"}); err == nil {
		t.Fatal("want error")
	}
}
