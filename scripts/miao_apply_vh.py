#!/usr/bin/env python3
"""
miao_apply_vh.py — Tự động áp bản vá Việt hóa (IPS32 vào ExeFS/main + 16 file .cas vào RomFS.img)
cho EA SPORTS FC 27 (1.8d.310c / v393216) trong luồng build Codemagic.

Cách dùng:
  1) Cài hook vào build_ipa_from_nsp.py:
       python3 scripts/miao_apply_vh.py --hook-builder engine/scripts/build_ipa_from_nsp.py --vh-dir "$VH_DIR" --engine-dir "$ENGINE_DIR"
  2) Hoặc chạy trực tiếp trên thư mục extract/<version>:
       python3 scripts/miao_apply_vh.py --extract-dir "<...>/extract/v393216" --vh-dir "$VH_DIR" --engine-dir "$ENGINE_DIR"
"""

import argparse
import hashlib
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path


def parse_ips(ips_bytes: bytes):
    """Parse chuẩn IPS hoặc IPS32 (Atmosphère Switch). Trả về list (offset, data_bytes)."""
    if ips_bytes.startswith(b"IPS32"):
        is_32 = True
        pos = 5
        eof_marker = b"EEOF"
    elif ips_bytes.startswith(b"PATCH"):
        is_32 = False
        pos = 5
        eof_marker = b"EOF"
    else:
        raise ValueError("File .ips không hợp lệ (thiếu header IPS32 hoặc PATCH).")

    hunks = []
    n = len(ips_bytes)
    while pos < n:
        if ips_bytes[pos : pos + len(eof_marker)] == eof_marker:
            break
        if is_32:
            if pos + 4 > n:
                break
            offset = struct.unpack(">I", ips_bytes[pos : pos + 4])[0]
            pos += 4
        else:
            if pos + 3 > n:
                break
            offset = (ips_bytes[pos] << 16) | (ips_bytes[pos + 1] << 8) | ips_bytes[pos + 2]
            pos += 3

        if pos + 2 > n:
            raise ValueError(f"IPS32 bị cắt cụt ở offset 0x{offset:x}")
        size = struct.unpack(">H", ips_bytes[pos : pos + 2])[0]
        pos += 2

        if size == 0:
            # RLE hunk
            rle_size = struct.unpack(">H", ips_bytes[pos : pos + 2])[0]
            rle_val = ips_bytes[pos + 2 : pos + 3]
            pos += 3
            hunks.append((offset, rle_val * rle_size))
        else:
            data = ips_bytes[pos : pos + size]
            pos += size
            hunks.append((offset, data))
    return hunks


def apply_ips32_to_nso(nso_path: Path, ips_path: Path) -> None:
    """
    Giải nén LZ4 các segment của file NSO0 (main), áp bản vá Atmosphère IPS32
    (offset tính theo uncompressed NSO image có bao gồm 0x100 byte NSO header),
    cập nhật lại SHA-256 từng segment và ghi ra file NSO0 không nén hợp lệ.
    """
    raw = nso_path.read_bytes()
    if raw[:4] != b"NSO0":
        raise RuntimeError(f"{nso_path} không phải định dạng NSO0 (magic={raw[:4]!r}).")

    flags = struct.unpack_from("<I", raw, 0x0C)[0]
    build_id = raw[0x40:0x60].hex().upper()
    ips_stem = ips_path.stem.upper()
    if not build_id.startswith(ips_stem):
        print(
            f"[WARN] Build ID của main ({build_id[:32]}) không khớp tên file IPS ({ips_stem})! "
            "Kiểm tra lại xem UPDATE_URL có đúng bản 1.8d.310c (v393216) không."
        )
    else:
        print(f"[VH] Xác nhận khớp Build ID 100%: {ips_stem}")

    # Đọc 3 segment: text (0x10), rodata (0x20), data (0x30)
    segs = []
    for idx, (hdr_off, fsize_off, comp_bit, hash_bit, hash_off) in enumerate(
        [
            (0x10, 0x60, 0x01, 0x08, 0xA0),
            (0x20, 0x64, 0x02, 0x10, 0xC0),
            (0x30, 0x68, 0x04, 0x20, 0xE0),
        ]
    ):
        file_off, mem_off, decomp_size = struct.unpack_from("<III", raw, hdr_off)
        comp_size = struct.unpack_from("<I", raw, fsize_off)[0]
        blob = raw[file_off : file_off + comp_size]
        if flags & comp_bit:
            try:
                import lz4.block
            except ImportError as exc:
                raise RuntimeError("Thiếu thư viện Python 'lz4' để giải nén NSO0!") from exc
            data = bytearray(lz4.block.decompress(blob, uncompressed_size=decomp_size))
        else:
            data = bytearray(blob)
        if len(data) != decomp_size:
            raise RuntimeError(
                f"Segment {idx} giải nén sai kích thước ({len(data)} != {decomp_size})"
            )
        segs.append(
            {
                "idx": idx,
                "hdr_off": hdr_off,
                "fsize_off": fsize_off,
                "comp_bit": comp_bit,
                "hash_bit": hash_bit,
                "hash_off": hash_off,
                "mem_off": mem_off,
                "decomp_size": decomp_size,
                "data": data,
            }
        )

    # Chuẩn Atmosphère: IPS32 offset = 0x100 + vaddr trong NSO
    hunks = parse_ips(ips_path.read_bytes())
    applied = 0
    for offset, patch_data in hunks:
        if offset < 0x100:
            raise RuntimeError(f"IPS32 offset 0x{offset:x} nằm trong NSO header (< 0x100)!")
        vaddr = offset - 0x100
        placed = False
        for seg in segs:
            s_start = seg["mem_off"]
            s_end = s_start + seg["decomp_size"]
            if s_start <= vaddr and (vaddr + len(patch_data)) <= s_end:
                rel = vaddr - s_start
                seg["data"][rel : rel + len(patch_data)] = patch_data
                applied += 1
                placed = True
                break
        if not placed:
            raise RuntimeError(
                f"IPS32 hunk vaddr=0x{vaddr:x} (len={len(patch_data)}) không nằm trong segment nào của main!"
            )

    # Dựng lại file NSO0 không nén (tắt 3 bit nén 0x1|0x2|0x4, cập nhật file_off, comp_size và SHA-256)
    new_hdr = bytearray(raw[:0x100])
    new_flags = flags & ~0x07
    struct.pack_into("<I", new_hdr, 0x0C, new_flags)

    first_orig_off = min(struct.unpack_from("<I", raw, s["hdr_off"])[0] for s in segs)
    prefix_extra = raw[0x100:first_orig_off] if first_orig_off > 0x100 else b""

    out = bytearray(new_hdr)
    out.extend(prefix_extra)

    for seg in segs:
        cur_off = len(out)
        seg_bytes = bytes(seg["data"])
        struct.pack_into("<I", out, seg["hdr_off"], cur_off)
        struct.pack_into("<I", out, seg["fsize_off"], len(seg_bytes))
        if new_flags & seg["hash_bit"]:
            digest = hashlib.sha256(seg_bytes).digest()
            out[seg["hash_off"] : seg["hash_off"] + 32] = digest
        out.extend(seg_bytes)

    nso_path.write_bytes(bytes(out))
    print(f"[VH] Đã áp thành công {applied} bản vá IPS32 vào {nso_path} ({len(out):,} bytes).")


def find_vh_roots(vh_dir: Path):
    """Tự động tìm thư mục exefs/ và romfs/ bên trong gói VH đã giải nén."""
    exefs_dir = None
    romfs_dir = None
    for root, dirs, _ in os.walk(vh_dir):
        for d in dirs:
            low = d.lower()
            p = Path(root) / d
            if low == "exefs" and exefs_dir is None:
                exefs_dir = p
            elif low == "romfs" and romfs_dir is None:
                romfs_dir = p
    return exefs_dir, romfs_dir


def patch_romfs_img(romfs_img: Path, vh_romfs_dir: Path, engine_dir: Path) -> None:
    """
    Ghi đè toàn bộ file từ vh_romfs_dir (16 file .cas) vào RomFS.img.
    Ưu tiên bung bằng dm/tools/romfs_image.py nếu có, hoặc đọc trực tiếp định dạng DMROMFS1.
    """
    romfs_tool = engine_dir / "dm/tools/romfs_image.py"
    if not romfs_tool.is_file():
        raise RuntimeError(f"Không tìm thấy {romfs_tool} để đóng gói lại RomFS.img!")

    vh_files = [p for p in vh_romfs_dir.rglob("*") if p.is_file()]
    if not vh_files:
        print("[VH] Không có file nào trong thư mục romfs/ của gói VH, bỏ qua.")
        return

    print(f"[VH] Tìm thấy {len(vh_files)} file trong romfs/ VH. Đang bung RomFS.img để ghép...")

    help_out = subprocess.run(
        [sys.executable, str(romfs_tool), "--help"],
        capture_output=True,
        text=True,
    ).stdout

    tmp_root = Path(tempfile.mkdtemp(prefix="miao_romfs_", dir=str(romfs_img.parent)))
    try:
        extracted = False
        for flag in ("--extract", "extract", "--unpack", "unpack"):
            if flag in help_out:
                cmd = [sys.executable, str(romfs_tool), flag, str(romfs_img), str(tmp_root)]
                r = subprocess.run(cmd, capture_output=True, text=True)
                if r.returncode == 0 and any(tmp_root.iterdir()):
                    extracted = True
                    break

        if not extracted:
            _unpack_dmromfs(romfs_img, tmp_root, romfs_tool)

        replaced = 0
        added = 0
        for src in vh_files:
            rel = src.relative_to(vh_romfs_dir)
            dst = tmp_root / rel
            if not dst.exists():
                matched = _find_case_insensitive(tmp_root, rel)
                if matched is not None:
                    dst = matched
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                replaced += 1
            else:
                added += 1
            shutil.copy2(src, dst)

        print(f"[VH] Đã ghép RomFS: ghi đè {replaced} file, thêm mới {added} file.")

        new_img = romfs_img.with_suffix(".img.vh_tmp")
        pack_cmd = [sys.executable, str(romfs_tool), str(tmp_root), str(new_img)]
        r = subprocess.run(pack_cmd, capture_output=True, text=True)
        if r.returncode != 0:
            pack_cmd2 = [sys.executable, str(romfs_tool), str(tmp_root), "-o", str(new_img)]
            r2 = subprocess.run(pack_cmd2, capture_output=True, text=True)
            if r2.returncode != 0:
                raise RuntimeError(
                    f"Lỗi đóng gói lại RomFS.img:\n{r.stderr}\n{r2.stderr}"
                )

        new_img.replace(romfs_img)
        print(f"[VH] Đã đóng gói lại {romfs_img} thành công ({romfs_img.stat().st_size / (1024**3):.2f} GiB).")
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _find_case_insensitive(root: Path, rel: Path):
    cur = root
    for part in rel.parts:
        if not cur.is_dir():
            return None
        low = part.lower()
        hit = next((c for c in cur.iterdir() if c.name.lower() == low), None)
        if hit is None:
            return None
        cur = hit
    return cur


def _unpack_dmromfs(img_path: Path, out_dir: Path, romfs_tool: Path):
    """Đọc cấu trúc DMROMFS1 từ romfs_image.py để bung toàn bộ file ra out_dir."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("nx_romfs_image", str(romfs_tool))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    for fn_name in ("extract", "unpack", "extract_image"):
        fn = getattr(mod, fn_name, None)
        if callable(fn):
            fn(img_path, out_dir)
            if any(out_dir.iterdir()):
                return

    if hasattr(mod, "Reader"):
        with mod.Reader(img_path) as rd:
            for rel_path in rd.files():
                dst = out_dir / rel_path.lstrip("/")
                dst.parent.mkdir(parents=True, exist_ok=True)
                with open(dst, "wb") as f:
                    for chunk in rd.iter_bytes(rel_path):
                        f.write(chunk)
        return

    with open(img_path, "rb") as f:
        magic = f.read(8)
        raise RuntimeError(
            f"romfs_image.py không có sẵn hàm extract (magic={magic!r}). "
            "Vui lòng kiểm tra phiên bản dm/tools/romfs_image.py trong engine."
        )


def apply_vh(extract_dir: Path, vh_dir: Path, engine_dir: Path) -> None:
    exefs_vh, romfs_vh = find_vh_roots(vh_dir)
    if not exefs_vh and not romfs_vh:
        raise RuntimeError(f"Không tìm thấy thư mục exefs/ hoặc romfs/ trong {vh_dir}!")

    if exefs_vh:
        exefs_target = extract_dir / "ExeFS"
        if not exefs_target.is_dir():
            exefs_target = extract_dir / "exefs"
        main_bin = exefs_target / "main"
        if not main_bin.is_file():
            raise RuntimeError(f"Không tìm thấy {main_bin} sau bước extract!")

        ips_files = sorted(exefs_vh.glob("*.ips"))
        if ips_files:
            for ips in ips_files:
                print(f"[VH] Áp bản vá IPS32: {ips.name} -> {main_bin}")
                apply_ips32_to_nso(main_bin, ips)
        elif (exefs_vh / "main").is_file():
            print(f"[VH] Chép đè trực tiếp exefs/main -> {main_bin}")
            shutil.copy2(exefs_vh / "main", main_bin)
        else:
            print(f"[VH] Cảnh báo: không thấy file .ips hay main trong {exefs_vh}")

    if romfs_vh:
        romfs_img = extract_dir / "RomFS.img"
        if romfs_img.is_file():
            patch_romfs_img(romfs_img, romfs_vh, engine_dir)
        else:
            romfs_folder = extract_dir / "RomFS"
            if romfs_folder.is_dir():
                for src in romfs_vh.rglob("*"):
                    if src.is_file():
                        rel = src.relative_to(romfs_vh)
                        dst = romfs_folder / rel
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, dst)
                print(f"[VH] Đã chép đè RomFS trực tiếp vào {romfs_folder}.")
            else:
                raise RuntimeError(f"Không tìm thấy RomFS.img hoặc thư mục RomFS trong {extract_dir}!")


def hook_builder(builder_path: Path, vh_dir: Path, script_path: Path, engine_dir: Path) -> None:
    """
    Tiêm hook gọi miao_apply_vh.py vào build_ipa_from_nsp.py ngay trước bước rewrite (patch_nso.py).
    """
    src = builder_path.read_text(encoding="utf-8")
    marker = "# MIAO_VH_HOOK_INJECTED"
    if marker in src:
        print("[VH] Hook đã có sẵn trong build_ipa_from_nsp.py.")
        return

    m = re.search(r"^([ \t]*).*patch_nso\.py", src, flags=re.MULTILINE)
    if not m:
        raise RuntimeError("Không tìm thấy vị trí gọi patch_nso.py trong build_ipa_from_nsp.py để chèn hook VH!")

    indent = m.group(1)
    hook_code = (
        f"{indent}{marker}\n"
        f"{indent}if os.environ.get('MIAO_VH_APPLIED') != '1':\n"
        f"{indent}    import glob as _mglob\n"
        f"{indent}    _cands = sorted(_mglob.glob(os.path.join(str(work), 'extract', 'v*')))\n"
        f"{indent}    if _cands:\n"
        f"{indent}        subprocess.run([sys.executable, {str(script_path)!r}, "
        f"'--extract-dir', _cands[-1], '--vh-dir', {str(vh_dir)!r}, '--engine-dir', {str(engine_dir)!r}], check=True)\n"
        f"{indent}        os.environ['MIAO_VH_APPLIED'] = '1'\n"
    )
    patched = src[: m.start()] + hook_code + src[m.start() :]
    builder_path.write_text(patched, encoding="utf-8")
    print(f"[VH] Đã chèn hook Việt hóa vào {builder_path} ngay trước bước rewrite.")


def main():
    ap = argparse.ArgumentParser(description="Áp bản vá Việt hóa FC 27 (IPS32 + RomFS .cas)")
    ap.add_argument("--extract-dir", type=Path, help="Thư mục extract/v<version> sau nx_extract")
    ap.add_argument("--vh-dir", type=Path, required=True, help="Thư mục chứa gói VH đã giải nén")
    ap.add_argument("--engine-dir", type=Path, required=True, help="Thư mục engine nxnative")
    ap.add_argument("--hook-builder", type=Path, help="Chèn hook vào scripts/build_ipa_from_nsp.py")
    args = ap.parse_args()

    if args.hook_builder:
        hook_builder(args.hook_builder, args.vh_dir.resolve(), Path(__file__).resolve(), args.engine_dir.resolve())
    elif args.extract_dir:
        apply_vh(args.extract_dir.resolve(), args.vh_dir.resolve(), args.engine_dir.resolve())
    else:
        ap.error("Cần truyền --hook-builder hoặc --extract-dir")


if __name__ == "__main__":
    main()
