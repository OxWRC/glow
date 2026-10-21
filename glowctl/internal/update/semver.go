package update

import (
	"regexp"
	"strconv"
)

// tagRe is a release tag vX.Y.Z (no leading zeros, no pre-release).
var tagRe = regexp.MustCompile(`^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$`)

// Compare returns -1, 0 or 1 as a is older than, the same as or newer than b;
// ok is false unless both are release tags vX.Y.Z.
func Compare(a, b string) (cmp int, ok bool) {
	ma, mb := tagRe.FindStringSubmatch(a), tagRe.FindStringSubmatch(b)
	if ma == nil || mb == nil {
		return 0, false
	}
	for i := 1; i <= 3; i++ {
		x, errx := strconv.Atoi(ma[i])
		y, erry := strconv.Atoi(mb[i])
		if errx != nil || erry != nil {
			return 0, false
		}
		if x != y {
			if x > y {
				return 1, true
			}
			return -1, true
		}
	}
	return 0, true
}
