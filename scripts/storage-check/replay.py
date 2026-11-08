"""Every state a power cut can leave on a disk, from a dm-log-writes log of what was written to it
(the kernel's Documentation/admin-guide/device-mapper/log-writes.rst; the technique xfstests uses).

    replay.py BASE_IMAGE LOG_DEVICE OUT_DIR

The writes between the log's marks "start" and "end" are grouped by the flushes that make them durable. For
each write it writes these states to OUT_DIR (state-<n>.img, with what it is in state-<n>.txt): a cut inside
it after each of its sectors (a card writes one sector at a time), a cut right after it (every write before
it there), and it alone after the epochs before (a card may persist a later write of an epoch before an
earlier one). Then the end state. Prints how many."""
import shutil
import struct
import sys

FLUSH, FUA, DISCARD, MARK = 1, 2, 4, 8
MAGIC = 0x6A736677736872
SECTOR = 512


def read_log(path: str) -> list[tuple]:
    """The log's entries: ("mark", name) or ("io", sector, data, flags)."""
    with open(path, "rb") as f:
        log = f.read()
    magic, _version, nr_entries, sectorsize = struct.unpack_from("<QQQI", log, 0)
    assert magic == MAGIC, f"not a dm-log-writes log (magic {magic:#x})"
    assert sectorsize == SECTOR, sectorsize
    entries, pos = [], SECTOR
    for _ in range(nr_entries):
        sector, nr_sectors, flags, data_len = struct.unpack_from("<QQQQ", log, pos)
        head = log[pos + 32:pos + SECTOR]
        pos += SECTOR
        if flags & MARK:
            entries.append(("mark", head[:data_len].decode()))
            continue
        data = b""
        if not flags & DISCARD and nr_sectors:
            data = log[pos:pos + nr_sectors * SECTOR]
            pos += nr_sectors * SECTOR
        entries.append(("io", sector, data, flags))
    return entries


def epochs_between_marks(entries: list[tuple]) -> list[list[tuple[int, bytes]]]:
    names = [e[1] if e[0] == "mark" else None for e in entries]
    ios = [e for e in entries[names.index("start") + 1:names.index("end")] if e[0] == "io"]
    epochs, current = [], []
    for _, sector, data, flags in ios:
        if data:
            current.append((sector, data))
        if flags & (FLUSH | FUA):
            epochs.append(current)
            current = []
    return [*epochs, current]


def main() -> None:
    base, log_dev, out = sys.argv[1:4]
    epochs = epochs_between_marks(read_log(log_dev))
    count = 0

    def state(writes: list[tuple[int, bytes]], what: str) -> None:
        nonlocal count
        path = f"{out}/state-{count:04d}"
        with open(path + ".txt", "w") as f:
            f.write(what)
        shutil.copyfile(base, path + ".img")
        with open(path + ".img", "r+b") as f:
            for sector, data in writes:
                f.seek(sector * SECTOR)
                f.write(data)
        count += 1

    done: list[tuple[int, bytes]] = []
    for e, epoch in enumerate(epochs):
        for i, (sector, data) in enumerate(epoch):
            # A card writes one sector at a time: a write of several can be cut after any of them
            for k in range(1, len(data) // SECTOR):
                state([*done, *epoch[:i], (sector, data[:k * SECTOR])],
                      f"epoch {e}: cut inside write {i} (sector {sector}), after {k} of {len(data) // SECTOR} sectors")
            state(done + epoch[:i + 1], f"epoch {e}: cut after write {i} (sector {sector})")
            if i:
                state([*done, epoch[i]], f"epoch {e}: write {i} (sector {sector}) alone, of sectors "
                                         f"{[s for s, _ in epoch]}")
        done += epoch
    state(done, "everything written")
    print(f"{count} states from {sum(len(e) for e in epochs)} writes in {len(epochs)} flush epochs")


if __name__ == "__main__":
    main()
