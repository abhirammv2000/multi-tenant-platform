package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"strings"
)

// Signer signs the pod IP stored in the sticky cookie, so a client can't point
// itself at an arbitrary address by editing the cookie.
type Signer struct {
	secret []byte
}

func NewSigner(secret string) Signer {
	return Signer{secret: []byte(secret)}
}

func (s Signer) mac(value string) string {
	h := hmac.New(sha256.New, s.secret)
	h.Write([]byte(value))
	return hex.EncodeToString(h.Sum(nil))
}

// Sign returns "value.signature". The format matches the Node proxy this
// replaces, so cookies issued by either one are accepted by the other.
func (s Signer) Sign(value string) string {
	return value + "." + s.mac(value)
}

// Verify returns the value if the signature is valid. The comparison is
// constant time, so it doesn't leak how much of a forged signature was right.
func (s Signer) Verify(signed string) (string, bool) {
	i := strings.LastIndex(signed, ".")
	if i == -1 {
		return "", false
	}
	value, sig := signed[:i], signed[i+1:]
	if !hmac.Equal([]byte(sig), []byte(s.mac(value))) {
		return "", false
	}
	return value, true
}
