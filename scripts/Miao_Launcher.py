#!/usr/bin/env python3
"""
miao_launcher.py — Tùy biến Launcher iOS & settings.tsv cho EA SPORTS FC 27:
  - Ảnh nền toàn màn hình (miao_bg.jpg / .png) + giao diện kính mờ (.ultraThinMaterial pha hồng pastel)
  - Thẻ Floating Credit "Port by miaomiaocutene" + icon mèo hồng ghim ở mép trên:
      * Không có dòng "Nhấn để mở Discord" (chạm vào thẻ vẫn mở Discord)
      * Khi cuộn xuống: trôi theo và tự thu gọn + giảm độ mờ (~0.58)
  - Cài đặt đồ họa Việt hóa: Docked 1080p / Handheld 720p, 60 / 30 fps, MetalFX / Standard,
    Aniso 16x (DM_NVN_ANISO), Chỉnh màu (DM_MIAO_COLOR + credit miaomiaochinhmau),
    Chế độ Máy Cũ / Tiết kiệm RAM dưới 15 Pro (DM_MIAO_LOW_RAM)
  - Tự động chép Squad chuyển nhượng (miao_default_save/) vào Documents/save ở lần mở đầu tiên
    + nút "Cài Squad chuyển nhượng" 1 chạm trong Launcher
"""

import argparse
import os
import re
import shutil
import subprocess
from pathlib import Path


def patch_settings_tsv(engine_dir: Path) -> None:
    tsv = engine_dir / "dm/games/settings.tsv"
    if not tsv.is_file():
        print(f"[LAUNCHER] Cảnh báo: không tìm thấy {tsv}")
        return

    lines = tsv.read_text(encoding="utf-8").splitlines()
    out = []
    seen = set()

    for ln in lines:
        if not ln.strip() or ln.lstrip().startswith("#"):
            out.append(ln)
            continue
        parts = ln.split("\t")
        var = parts[0].strip()
        seen.add(var)

        if var == "DM_OPMODE" and len(parts) >= 8:
            parts[2] = "1=Docked (1080p),0=Handheld (720p)"
            parts[3] = "1"
            parts[6] = "Chế độ hiển thị (Độ phân giải)"
            out.append("\t".join(parts))
        elif var == "DM_FPS_TARGET" and len(parts) >= 8:
            parts[6] = "Tốc độ khung hình (FPS)"
            out.append("\t".join(parts))
        elif var == "DM_PRESENT_UPSCALE" and len(parts) >= 8:
            parts[2] = "off=Standard,metalfx=MetalFX"
            parts[3] = "metalfx"
            parts[6] = "Nâng nét (Upscaling)"
            parts[7] = "MetalFX làm nét cạnh cầu thủ và sân cỏ trên màn 2K."
            out.append("\t".join(parts))
        elif var == "DM_NVN_ANISO" and len(parts) >= 8:
            # Mở cho cả iOS (gốc chỉ có platform:mac)
            parts[2] = "0=Mặc định,16=Aniso 16x"
            parts[3] = "16"
            parts[4] = "gfxapi:nvn"
            parts[6] = "Lọc mặt cỏ (Aniso 16x)"
            parts[7] = "Giúp mặt cỏ và vạch vôi nhìn chéo từ xa sắc nét hơn."
            out.append("\t".join(parts))
        else:
            out.append(ln)

    # Thêm các dòng còn thiếu nếu chưa có trong settings.tsv
    if "DM_NVN_ANISO" not in seen:
        out.append(
            "DM_NVN_ANISO\tchoice\t0=Mặc định,16=Aniso 16x\t16\tgfxapi:nvn\tGame\t"
            "Lọc mặt cỏ (Aniso 16x)\tGiúp mặt cỏ và vạch vôi nhìn chéo từ xa sắc nét hơn.\tlevel=basic"
        )
    if "DM_MIAO_COLOR" not in seen:
        out.append(
            "DM_MIAO_COLOR\tchoice\t0=Mặc định (Gốc),1=Cỏ tươi sống động,2=Điện ảnh\t0\talways\tGame\t"
            "Chỉnh màu\tmiaomiaochinhmau\tlevel=basic"
        )
    if "DM_MIAO_LOW_RAM" not in seen:
        out.append(
            "DM_MIAO_LOW_RAM\ttoggle\t-\t0\tplatform:ios\tPerformance\t"
            "Chế độ Máy Cũ (dưới 15 Pro)\tTự hạ 720p, 30 fps, tắt MetalFX/MTL4 và bật tiết kiệm RAM.\tlevel=basic"
        )

    tsv.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"[LAUNCHER] Đã cập nhật {tsv} (Aniso 16x, Chỉnh màu miaomiaochinhmau, Chế độ Máy Cũ).")


def patch_main_m_low_ram(engine_dir: Path) -> None:
    """
    Khi DM_MIAO_LOW_RAM=1, ép các biến tiết kiệm RAM (720p, 30 fps, tắt MetalFX/MTL4, DM_GUEST_FILE=1)
    trước khi host khởi động.
    """
    main_m = engine_dir / "dm/ios/Sources/main.m"
    if not main_m.is_file():
        return
    src = main_m.read_text(encoding="utf-8")
    if "MIAO_LOW_RAM_HOOK" in src:
        return

    # Tìm chỗ đọc xong env / trước khi gọi dm_main hoặc boot_thread
    m = re.search(r"(dm_main\s*\(|boot_thread|nnosInitialize)", src)
    if not m:
        return

    # Tìm đầu dòng của câu lệnh chứa dm_main(
    line_start = src.rfind("\n", 0, m.start()) + 1
    indent = re.match(r"[ \t]*", src[line_start:]).group(0)
    hook = (
        f"{indent}// MIAO_LOW_RAM_HOOK\n"
        f"{indent}if (getenv(\"DM_MIAO_LOW_RAM\") && strcmp(getenv(\"DM_MIAO_LOW_RAM\"), \"1\") == 0) {{\n"
        f"{indent}    setenv(\"DM_OPMODE\", \"0\", 1);\n"
        f"{indent}    setenv(\"DM_FPS_TARGET\", \"30\", 1);\n"
        f"{indent}    setenv(\"DM_PRESENT_UPSCALE\", \"off\", 1);\n"
        f"{indent}    setenv(\"DM_PSO_MTL4\", \"0\", 1);\n"
        f"{indent}    setenv(\"DM_GUEST_FILE\", \"1\", 1);\n"
        f"{indent}}}\n"
    )
    main_m.write_text(src[:line_start] + hook + src[line_start:], encoding="utf-8")
    print(f"[LAUNCHER] Đã thêm xử lý DM_MIAO_LOW_RAM vào {main_m}.")


SWIFT_MIAO_DEFS = r"""
// === MIAO GLASS LAUNCHER + FLOATING CREDIT + AUTO SQUAD ===
private struct MiaoScrollOffsetKey: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

private struct MiaoCatIcon: Shape {
    func path(in rect: CGRect) -> Path {
        var p = Path()
        let w = rect.width, h = rect.height
        p.move(to: CGPoint(x: w * 0.16, y: h * 0.42))
        p.addLine(to: CGPoint(x: w * 0.22, y: h * 0.12))
        p.addLine(to: CGPoint(x: w * 0.40, y: h * 0.30))
        p.addQuadCurve(to: CGPoint(x: w * 0.60, y: h * 0.30), control: CGPoint(x: w * 0.50, y: h * 0.26))
        p.addLine(to: CGPoint(x: w * 0.78, y: h * 0.12))
        p.addLine(to: CGPoint(x: w * 0.84, y: h * 0.42))
        p.addQuadCurve(to: CGPoint(x: w * 0.50, y: h * 0.88), control: CGPoint(x: w * 0.88, y: h * 0.82))
        p.addQuadCurve(to: CGPoint(x: w * 0.16, y: h * 0.42), control: CGPoint(x: w * 0.12, y: h * 0.82))
        p.closeSubpath()
        return p
    }
}

private struct MiaoFloatingCreditHeader: View {
    let scrolled: Bool
    private let discordURL = URL(string: "https://discord.com/users/893716299014873088")!

    var body: some View {
        Link(destination: discordURL) {
            HStack(spacing: 8) {
                MiaoCatIcon()
                    .fill(Color(red: 0.93, green: 0.28, blue: 0.58))
                    .frame(width: scrolled ? 18 : 24, height: scrolled ? 18 : 24)
                Text("Port by miaomiaocutene")
                    .font(scrolled ? .subheadline.weight(.bold) : .title3.weight(.bold))
                    .foregroundColor(Color(red: 0.72, green: 0.12, blue: 0.42))
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .center)
            .padding(.vertical, scrolled ? 8 : 13)
            .padding(.horizontal, 16)
            .background(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(.ultraThinMaterial)
                    .overlay(
                        RoundedRectangle(cornerRadius: 16, style: .continuous)
                            .fill(Color(red: 1.0, green: 0.84, blue: 0.91).opacity(scrolled ? 0.35 : 0.65))
                    )
            )
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .stroke(Color.white.opacity(scrolled ? 0.45 : 0.75), lineWidth: 1.0)
            )
            .shadow(color: Color.black.opacity(scrolled ? 0.08 : 0.14), radius: 8, y: 3)
        }
        .buttonStyle(.plain)
        .opacity(scrolled ? 0.58 : 1.0)
        .padding(.horizontal, 14)
        .padding(.top, 6)
        .padding(.bottom, 4)
        .animation(.easeInOut(duration: 0.22), value: scrolled)
    }
}

private enum MiaoSquadInstaller {
    static func bundledSaveURL() -> URL? {
        if let u = Bundle.main.url(forResource: "miao_default_save", withExtension: nil) {
            return u
        }
        return Bundle.main.resourceURL?.appendingPathComponent("miao_default_save")
    }

    static func documentsSaveURL() -> URL {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        return docs.appendingPathComponent("save", isDirectory: true)
    }

    @discardableResult
    static func autoInstallIfEmpty() -> Bool {
        let fm = FileManager.default
        guard let src = bundledSaveURL(), fm.fileExists(atPath: src.path) else { return false }
        let dst = documentsSaveURL()
        if let items = try? fm.contentsOfDirectory(atPath: dst.path), !items.isEmpty {
            return false // Đã có save cũ, không ghi đè
        }
        return copyDirectoryContents(from: src, to: dst)
    }

    static func installSquadOverwrite() -> String {
        let fm = FileManager.default
        guard let src = bundledSaveURL(), fm.fileExists(atPath: src.path) else {
            return "Chưa nhúng gói Squad chuyển nhượng trong bản IPA này."
        }
        let dst = documentsSaveURL()
        // Nếu gói nhúng chỉ chứa 2 file DATA & INDEX trực tiếp, tìm thư mục con chứa DATA trong Documents/save
        let srcData = src.appendingPathComponent("DATA")
        let srcIndex = src.appendingPathComponent("INDEX")
        if fm.fileExists(atPath: srcData.path) && fm.fileExists(atPath: srcIndex.path) {
            if let subdirs = try? fm.contentsOfDirectory(at: dst, includingPropertiesForKeys: nil) {
                for sub in subdirs {
                    let targetData = sub.appendingPathComponent("DATA")
                    if fm.fileExists(atPath: targetData.path) {
                        try? fm.removeItem(at: targetData)
                        try? fm.copyItem(at: srcData, to: targetData)
                        let targetIndex = sub.appendingPathComponent("INDEX")
                        try? fm.removeItem(at: targetIndex)
                        try? fm.copyItem(at: srcIndex, to: targetIndex)
                        return "Đã cài Squad chuyển nhượng vào \(sub.lastPathComponent)!"
                    }
                }
            }
            // Nếu chưa có slot nào, chép cả vào Documents/save
            _ = copyDirectoryContents(from: src, to: dst)
            return "Đã chép DATA & INDEX. Nếu chưa hiện, hãy vào game Save Squads 1 lần rồi bấm lại nút này."
        }
        if copyDirectoryContents(from: src, to: dst) {
            return "Đã cài xong Squad chuyển nhượng! Vào game → Cài đặt → Hồ sơ → Load Squads."
        }
        return "Không thể chép file Squad chuyển nhượng."
    }

    private static func copyDirectoryContents(from src: URL, to dst: URL) -> Bool {
        let fm = FileManager.default
        do {
            try fm.createDirectory(at: dst, withIntermediateDirectories: true)
            let items = try fm.contentsOfDirectory(at: src, includingPropertiesForKeys: nil)
            for item in items {
                let target = dst.appendingPathComponent(item.lastPathComponent)
                if fm.fileExists(atPath: target.path) {
                    try? fm.removeItem(at: target)
                }
                try fm.copyItem(at: item, to: target)
            }
            return true
        } catch {
            return false
        }
    }
}

private struct MiaoSquadRow: View {
    @State private var statusText: String = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button(action: {
                statusText = MiaoSquadInstaller.installSquadOverwrite()
            }) {
                HStack {
                    Image(systemName: "arrow.down.doc.fill")
                        .foregroundColor(Color(red: 0.85, green: 0.22, blue: 0.52))
                    Text("Cài Squad chuyển nhượng")
                        .font(.body.weight(.semibold))
                        .foregroundColor(.primary)
                }
            }
            if !statusText.isEmpty {
                Text(statusText)
                    .font(.caption)
                    .foregroundColor(Color(red: 0.72, green: 0.12, blue: 0.42))
            }
        }
    }
}

private struct MiaoGlassBackgroundModifier: ViewModifier {
    @State private var isScrolled: Bool = false

    private func loadBackgroundImage() -> UIImage? {
        for name in ["miao_bg.jpg", "miao_bg.png", "launcher_bg.jpg", "launcher_bg.png"] {
            let comps = name.split(separator: ".")
            if comps.count == 2,
               let path = Bundle.main.path(forResource: String(comps[0]), ofType: String(comps[1])),
               let img = UIImage(contentsOfFile: path) {
                return img
            }
        }
        return nil
    }

    @ViewBuilder
    private var backdrop: some View {
        ZStack {
            if let uiImg = loadBackgroundImage() {
                Image(uiImage: uiImg)
                    .resizable()
                    .scaledToFill()
                    .ignoresSafeArea()
                Color(red: 1.0, green: 0.88, blue: 0.94)
                    .opacity(0.22)
                    .ignoresSafeArea()
            } else {
                LinearGradient(
                    colors: [
                        Color(red: 1.0, green: 0.93, blue: 0.96),
                        Color(red: 0.99, green: 0.86, blue: 0.92)
                    ],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
                .ignoresSafeArea()
            }
        }
    }

    func body(content: Content) -> some View {
        Group {
            if #available(iOS 16.0, *) {
                content
                    .scrollContentBackground(.hidden)
                    .background(backdrop)
                    .safeAreaInset(edge: .top, spacing: 0) {
                        MiaoFloatingCreditHeader(scrolled: isScrolled)
                    }
            } else {
                content
                    .background(backdrop)
                    .safeAreaInset(edge: .top, spacing: 0) {
                        MiaoFloatingCreditHeader(scrolled: isScrolled)
                    }
            }
        }
        .onPreferenceChange(MiaoScrollOffsetKey.self) { val in
            let nowScrolled = val < -12
            if nowScrolled != isScrolled {
                isScrolled = nowScrolled
            }
        }
        .onAppear {
            _ = MiaoSquadInstaller.autoInstallIfEmpty()
        }
    }
}
"""


def find_matching_brace(text: str, open_brace_idx: int) -> int:
    depth = 0
    i = open_brace_idx
    in_str = False
    while i < len(text):
        ch = text[i]
        if ch == '"' and (i == 0 or text[i - 1] != "\\"):
            in_str = not in_str
        elif not in_str:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return -1


def patch_launcher_swift(engine_dir: Path) -> None:
    candidates = [
        engine_dir / "dm/ios/Sources/Launcher.swift",
        engine_dir / "src/dm/ios/Sources/Launcher.swift",
    ]
    target = next((p for p in candidates if p.is_file()), None)
    if not target:
        raise RuntimeError("Không tìm thấy Launcher.swift trong engine!")

    orig = target.read_text(encoding="utf-8")
    if "MiaoGlassBackgroundModifier" in orig:
        print(f"[LAUNCHER] {target} đã được vá trước đó.")
        return

    patched = None
    for kind, pat in [
        ("form", r"Form\s*\{"),
        ("list", r"List\s*\{"),
        ("vstack", r"VStack(?:\([^)]*\))?\s*\{"),
    ]:
        m = re.search(pat, orig)
        if not m:
            continue
        open_idx = m.end() - 1
        close_idx = find_matching_brace(orig, open_idx)
        if close_idx == -1:
            continue

        # Chèn cảm biến cuộn + nút Squad chuyển nhượng (kính mờ) vào đầu Form/List
        if kind in ("form", "list"):
            top_inject = (
                "\n            Color.clear\n"
                "                .frame(height: 0)\n"
                "                .listRowBackground(Color.clear)\n"
                "                .listRowInsets(EdgeInsets())\n"
                "                .background(\n"
                "                    GeometryReader { geo in\n"
                "                        Color.clear.preference(\n"
                "                            key: MiaoScrollOffsetKey.self,\n"
                "                            value: geo.frame(in: .global).minY\n"
                "                        )\n"
                "                    }\n"
                "                )\n"
                "            Section {\n"
                "                MiaoSquadRow()\n"
                "            }\n"
                "            .listRowBackground(Rectangle().fill(.ultraThinMaterial).overlay(Color(red: 1.0, green: 0.90, blue: 0.95).opacity(0.35)))\n"
            )
            # Làm mờ kính cho các Section/Row bên trong Form
            inner = orig[m.end() : close_idx]
            inner_glass = re.sub(
                r"(Section\s*(?:\([^)]*\))?\s*\{)",
                r"\1",
                inner,
            )
        else:
            top_inject = "\n            MiaoSquadRow().padding(.horizontal, 14)\n"
            inner_glass = orig[m.end() : close_idx]

        # Thêm UIKit import nếu chưa có (cần cho UIImage)
        header_add = ""
        if "import UIKit" not in orig:
            header_add = "import UIKit\n"

        patched = (
            header_add
            + orig[: m.end()]
            + top_inject
            + inner_glass
            + orig[close_idx : close_idx + 1]
            + "\n        .listRowBackground(Rectangle().fill(.ultraThinMaterial).overlay(Color(red: 1.0, green: 0.92, blue: 0.96).opacity(0.32)))"
            + "\n        .modifier(MiaoGlassBackgroundModifier())"
            + orig[close_idx + 1 :]
            + "\n"
            + SWIFT_MIAO_DEFS
        )
        print(f"[LAUNCHER] Đã tiêm Glass UI + Floating Credit + Auto Squad vào {kind} của {target}")
        break

    if not patched:
        raise RuntimeError("Không tìm thấy Form/List/VStack trong Launcher.swift!")

    backup = target.with_suffix(".swift.bak")
    shutil.copy2(target, backup)
    target.write_text(patched, encoding="utf-8")

    # Kiểm tra cú pháp Swift bằng xcrun swiftc -parse (nếu đang chạy trên macOS)
    if shutil.which("xcrun"):
        res = subprocess.run(["xcrun", "swiftc", "-parse", str(target)], capture_output=True, text=True)
        if res.returncode != 0:
            print("ERROR: swiftc -parse lỗi trên Launcher.swift:\n", res.stderr)
            shutil.move(str(backup), str(target))
            raise SystemExit(1)
        print("[LAUNCHER] Kiểm tra cú pháp Launcher.swift (swiftc -parse): OK!")
    backup.unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser(description="Tiêm Glass Launcher + Settings Việt hóa + Auto Squad")
    ap.add_argument("--engine-dir", type=Path, required=True, help="Thư mục engine nxnative")
    args = ap.parse_args()
    engine_dir = args.engine_dir.resolve()
    patch_settings_tsv(engine_dir)
    patch_main_m_low_ram(engine_dir)
    patch_launcher_swift(engine_dir)


if __name__ == "__main__":
    main()