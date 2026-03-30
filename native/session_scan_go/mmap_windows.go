//go:build windows

package main

import (
	"fmt"
	"os"
)

// mmapFile is a no-op stub on Windows where syscall.Mmap is unavailable.
// It always returns an error, causing the caller to fall back to buffered I/O.
func mmapFile(f *os.File, size int64) (data []byte, unmap func(), err error) {
	return nil, nil, fmt.Errorf("mmap not supported on Windows; using buffered I/O")
}
