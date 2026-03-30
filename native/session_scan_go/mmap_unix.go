//go:build !windows

package main

import (
	"fmt"
	"math"
	"os"
	"syscall"
)

// mmapFile memory-maps a file and returns the mapped bytes and an unmap
// function.  On error it falls back by returning (nil, nil, err).
// Only called for files >= mmapThreshold bytes.
func mmapFile(f *os.File, size int64) (data []byte, unmap func(), err error) {
	if size <= 0 {
		return nil, func() {}, nil
	}
	// Guard against 32-bit int overflow: on platforms where int is 32 bits,
	// files larger than math.MaxInt32 (~2 GB) cannot be safely mmap'd via
	// syscall.Mmap (which takes an int length).  Fall back to buffered I/O
	// in that case by returning an error.
	if size > math.MaxInt32 && math.MaxInt == math.MaxInt32 {
		return nil, nil, fmt.Errorf("file too large for mmap on 32-bit platform: %d bytes", size)
	}
	data, err = syscall.Mmap(int(f.Fd()), 0, int(size), syscall.PROT_READ, syscall.MAP_SHARED)
	if err != nil {
		return nil, nil, err
	}
	return data, func() {
		_ = syscall.Munmap(data)
	}, nil
}
