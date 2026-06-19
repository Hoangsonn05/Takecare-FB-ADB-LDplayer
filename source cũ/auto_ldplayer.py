
    #!/usr/bin/env python3
    ldplayer_fb_automation.py
    ==============================
    LDPlayer + Facebook Newsfeed automation (all‑in‑one)
    ==============================
    Requirements:
    pip install opencv-python numpy
    LDPlayer installed (ldconsole.exe, ld.exe, adb.exe accessible)
    Template images for reactions (like.png, love.jpg, thuongthuong.jpg,
    haha.jpg, wow.jpg, sad.jpg, angry.jpg) – place them in a folder
    pointed to by --template-dir (default: ./templates)
    #
    Usage example:
    python ldplayer_fb_automation.py --index 4 --time 30 --prob 10 \
    --max-reactions 20 --reaction-type "like,love,haha,wow,sad,angry"
    ==============================

    import argparse
    import csv
    import datetime
    import os
    import random
    import subprocess
    import sys
    import time
    from pathlib import Path

    --------------------------------------------------------------
    Configuration (can be overridden via CLI)
    --------------------------------------------------------------
    DEFAULT_LD_BASE = r"D:\MMO\QuanlyLDPlayer\LD9Mod-32bit\LD9Mod"  # Windows default
    DEFAULT_TEMPLATE_DIR = str(Path(file).with_name("templates"))

    LDPlayer constants
    LD_EXE_NAME = "ld.exe"
    LDCONSOLE_EXE_NAME = "ldconsole.exe"
    ADB_EXE_NAME = "adb.exe"

    ADB port base for LDPlayer (index 0 -> 5555, index 1 -> 5557, …)
    ADB_PORT_BASE = 5555

    Swipe parameters (px, ms) – kept inside the script for easy tweaking
    SWIPE_X_MIN = 200
    SWIPE_X_MAX = 340
    SWIPE_Y_DOWN_1_MIN = 700
    SWIPE_Y_DOWN_1_MAX = 800
    SWIPE_Y_DOWN_2_MIN = 220
    SWIPE_Y_DOWN_2_MAX = 320
    SWIPE_Y_UP_1_MIN = 220
    SWIPE_Y_UP_1_MAX = 320
    SWIPE_Y_UP_2_MIN = 700
    SWIPE_Y_UP_2_MAX = 800
    SWIPE_DURATION_MIN = 350
    SWIPE_DURATION_MAX = 450
    SWIPE_DELAY_MIN = 2.0
    SWIPE_DELAY_MAX = 3.0

    Reaction configuration
    CONFIDENCE_LIKE = 0.60          # template match threshold for the Like button
    CONFIDENCE_REACTION = 0.60      # threshold for reaction icons
    REACTION_OFFSETS = {
        "like": (0, 0),
        "love": (60, -70),
        "thuongthuong": (90, -70),
        "haha": (120, -70),
        "wow": (180, -70),
        "sad": (240, -70),
        "angry": (300, -70),
    }

    --------------------------------------------------------------
    Helper functions – LDPlayer control
    --------------------------------------------------------------
    def _resolve_path(base: str, *parts: str) -> str:
        return str(Path(base).joinpath(*parts))

    def ldconsole_cmd(base: str, *args: str) -> str:
        exe = _resolve_path(base, LDCONSOLE_EXE_NAME)
        return f'"{exe}" ' + " ".join(args)

    def ld_cmd(base: str, *args: str) -> str:
        exe = _resolve_path(base, LD_EXE_NAME)
        return f'"{exe}" ' + " ".join(args)

    def adb_cmd(base: str, serial: str, *args: str) -> str:
        exe = _resolve_path(base, ADB_EXE_NAME)
        return f'"{exe}" -s {serial} ' + " ".join(args)

    def launch_instance(base: str, index: int) -> None:
        """Start LDPlayer instance (if not already running)."""
        print(f"[LDPlayer] Launching instance {index} …")
        subprocess.run(ldconsole_cmd(base, "launch", f"--index={index}"), shell=True, timeout=30)
        time.sleep(8)  # give LDPlayer time to boot

    def open_facebook(base: str, index: int) -> None:
        """Launch Facebook app inside the given instance."""
        print(f"[LDPlayer] Opening Facebook on instance {index} …")
        subprocess.run(
            ldconsole_cmd(base, "runapp", f"--index={index}", "--packagename=com.facebook.katana"),
            shell=True,
            timeout=20,
        )
        time.sleep(5)  # wait for FB to load

    def adb_serial(base: str, index: int) -> str:
        """Return the ADB serial string for the given LDPlayer index."""
        port = ADB_PORT_BASE + 2 * index
        return f"localhost:{port}"

    --------------------------------------------------------------
    Screenshot & template‑matching utilities
    --------------------------------------------------------------
    def _ensure_template(path: str, name: str) -> str:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Template '{name}' not found at {path}")
        return path

    def take_screenshot(base: str, serial: str, out_path: str) -> bool:
        """Capture screen via ADB and pull it to out_path."""
        try:
            subprocess.run(
                adb_cmd(base, serial, "shell", "screencap", "-p", "/sdcard/tmp_screen.png"),
                capture_output=True,
                timeout=10,
            )
            subprocess.run(
                adb_cmd(base, serial, "pull", "/sdcard/tmp_screen.png", out_path),
                capture_output=True,
                timeout=10,
            )
            return os.path.isfile(out_path)
        except Exception as e:
            print(f"[ERROR] Screenshot failed: {e}")
            return False

    def find_like_button(img_path: str, template_path: str, confidence: float):
        """Return (x, y) centre of the Like button or None."""
        import cv2
        import numpy as np

        if not os.path.isfile(template_path):
            print(f"[ERROR] Like template missing: {template_path}")
            return None
        if not os.path.isfile(img_path):
            return None

        template = cv2.imread(template_path, cv2.IMREAD_GRAYSCALE)
        if template is None:
            print(f"[ERROR] Could not read Like template: {template_path}")
            return None
        t_w, t_h = template.shape[::-1]

        img = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return None

        res = cv2.matchTemplate(img, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if max_val >= confidence:
            cx = max_loc[0] + t_w // 2
            cy = max_loc[1] + t_h // 2
            print(f"[INFO] Like button found at ({cx}, {cy}) confidence={max_val:.2f}")
            return cx, cy
        else:
            print(f"[INFO] Like button not found (best confidence={max_val:.2f})")
            return None

    def find_reaction(img_path: str, tmpl_path: str, confidence: float):
        """Return (x, y) centre of a reaction icon or None."""
        import cv2
        import numpy as np

        if not tmpl_path or not os.path.isfile(tmpl_path):
            return None
        template = cv2.imread(tmpl_path, cv2.IMREAD_GRAYSCALE)
        if template is None:
            return None
        t_w, t_h = template.shape[::-1]

        img = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return None

        res = cv2.matchTemplate(img, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if max_val >= confidence:
            cx = max_loc[0] + t_w // 2
            cy = max_loc[1] + t_h // 2
            print(f"[INFO] Reaction found at ({cx}, {cy}) confidence={max_val:.2f}")
            return cx, cy
        return None

    def tap_ld(base: str, index: int, x: int, y: int, duration: int = 50):
        """Tap via ld.exe input swipe (same start/end)."""
        cmd = ld_cmd(base, "-s", str(index), "input", "swipe", f"{x}", f"{y}", f"{x}", f"{y}", str(duration))
        subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def long_press_ld(base: str, index: int, x: int, y: int, duration_ms: int = 1000):
        cmd = ld_cmd(base, "-s", str(index), "input", "swipe", f"{x}", f"{y}", f"{x}", f"{y}", str(duration_ms))
        subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def swipe_ld(base: str, index: int, x1: int, y1: int, x2: int, y2: int, duration_ms: int):
        cmd = ld_cmd(
            base,
            "-s",
            str(index),
            "input",
            "swipe",
            f"{x1}",
            f"{y1}",
            f"{x2}",
            f"{y2}",
            str(duration_ms),
        )
        subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    --------------------------------------------------------------
    Core scrolling & reaction logic (mirrors nurture_scroll.py)
    --------------------------------------------------------------
    def random_swipe_down():
        x1 = random.randint(SWIPE_X_MIN, SWIPE_X_MAX)
        y1 = random.randint(SWIPE_Y_DOWN_1_MIN, SWIPE_Y_DOWN_1_MAX)
        x2 = random.randint(SWIPE_X_MIN, SWIPE_X_MAX)
        y2 = random.randint(SWIPE_Y_DOWN_2_MIN, SWIPE_Y_DOWN_2_MAX)
        dur = random.randint(SWIPE_DURATION_MIN, SWIPE_DURATION_MAX)
        delay = random.uniform(SWIPE_DELAY_MIN, SWIPE_DELAY_MAX)
        return x1, y1, x2, y2, dur, delay

    def random_swipe_up():
        x1 = random.randint(SWIPE_X_MIN, SWIPE_X_MAX)
        y1 = random.randint(SWIPE_Y_UP_1_MIN, SWIPE_Y_UP_1_MAX)
        x2 = random.randint(SWIPE_X_MIN, SWIPE_X_MAX)
        y2 = random.randint(SWIPE_Y_UP_2_MIN, SWIPE_Y_UP_2_MAX)
        dur = random.randint(SWIPE_DURATION_MIN, SWIPE_DURATION_MAX)
        delay = random.uniform(SWIPE_DELAY_MIN, SWIPE_DELAY_MAX)
        return x1, y1, x2, y2, dur, delay

    def do_swipe(base, index, x1, y1, x2, y2, dur, delay, label=""):
        print(f"{label}({x1},{y1})->({x2},{y2}) | {dur}ms | wait {delay:.1f}s")
        swipe_ld(base, index, x1, y1, x2, y2, dur)
        time.sleep(delay)

    def add_reaction(
        base,
        index,
        serial,
        screenshot_path,
        like_template,
        reaction_templates,
        reaction_type,
        total_reactions,
        max_reactions,
        like_pos,
        forced=None,
    ):
        """Attempt to drop a reaction. Returns True if a reaction was sent."""
        if total_reactions >= max_reactions:
            print("  Reaction limit reached – skipping")
            return False

        if not take_screenshot(base, serial, screenshot_path):
            return False

        like_x, like_y = like_pos
        # Choose reaction type
        if forced is not None:
            chosen = forced
            print(f"  Forced reaction: {chosen}")
        else:
            if reaction_type == "random":
                pool = list(REACTION_OFFSETS.keys())
            else:
                pool = [r.strip() for r in reaction_type.split(",")]
            chosen = random.choice(pool)
            print(f"  Selected reaction: {chosen}")

        # Mandatory 1.5 s pause before attempting reaction
        print("  Waiting 1.5 s before reaction …")
        time.sleep(1.5)

        if chosen == "like":
            tap_ld(base, index, like_x, like_y)
            time.sleep(1)
            return True
        else:
            # Open reaction panel via long‑press on Like button
            print(f"  Long‑press Like at ({like_x},{like_y}) to open reactions")
            long_press_ld(base, index, like_x, like_y, 1000)
            time.sleep(2.5)  # wait for panel to appear

            # Re‑capture screen (now contains reaction icons)
            if not take_screenshot(base, serial, screenshot_path):
                print("  Failed to re‑capture screen – using static offset")
                use_static = True
            else:
                use_static = False

            if not use_static:
                tmpl = reaction_templates.get(chosen)
                pos = find_reaction(screenshot_path, tmpl, CONFIDENCE_REACTION)
                if pos:
                    rx, ry = pos
                    print(f"  Tapping reaction {chosen} at ({rx},{ry})")
                    tap_ld(base, index, rx, ry)
                    time.sleep(1)
                    return True
                else:
                    print(f"  Template match failed for {chosen} – falling back to static offset")
                    use_static = True

            if use_static:
                if chosen not in REACTION_OFFSETS:
                    print(f"  Unknown reaction {chosen} – defaulting to Like")
                    tap_ld(base, index, like_x, like_y)
                    return True
                ox, oy = REACTION_OFFSETS[chosen]
                rx, ry = like_x + ox, like_y + oy
                print(f"  Using static offset for {chosen}: ({rx},{ry})")
                tap_ld(base, index, rx, ry)
                time.sleep(1)
                return True

    def scroll_newsfeed(
        base,
        index,
        serial,
        screenshot_path,
        like_template,
        reaction_templates,
        minutes,
        prob_percent,
        max_reactions,
        reaction_type,
    ):
        """Main loop – swipes newsfeed and drops reactions according to spec."""
        start = time.time()
        end = start + minutes * 60

        counted_posts = 0
        total_reactions = 0
        swipes_needed = 0   # swipes needed for current post
        swipes_done = 0     # swipes already performed for current post

        # Build reaction queue if a specific list was given
        reaction_queue = []
        if reaction_type != "random":
            reaction_queue = [r.strip() for r in reaction_type.split(",")]
            random.shuffle(reaction_queue)
            print(f"[INFO] Reaction queue: {reaction_queue}")

        print(
            f"\n[START] Running for {minutes} min | "
            f"Reaction prob: {prob_percent}% | Max reactions: {max_reactions} | Type: {reaction_type}"
        )
        print("[LOGIC] 1‑2 swipes/post (avg 1.5) | 10‑15 % posts get an extra up‑then‑down\n")

        while time.time() < end:
            # If we have satisfied the swipe count for the current post, start a new post
            if swipes_done >= swipes_needed:
                counted_posts += 1
                swipes_needed = random.randint(1, 2)   # 1 or 2 swipes per post
                swipes_done = 0

                print(f"[Post {counted_posts}] Needs {swipes_needed} swipe(s) down", end="")

                # Random 10‑15 % chance: add an up‑then‑down pair (does NOT count toward post)
                if random.randint(1, 100) <= 15:
                    print(" | [EXTRA] Up + Down", end="")
                    xu1, yu1, xu2, yu2, udu, udelay = random_swipe_up()
                    do_swipe(base, index, xu1, yu1, xu2, yu2, udu, udelay, "[Up-Random]")
                    time.sleep(1)
                    xd1, yd1, xd2, yd2, ddu, ddelay = random_swipe_down()
                    do_swipe(base, index, xd1, yd1, xd2, yd2, ddu, ddelay, "[Down-Hoàn trả]")
                    time.sleep(1)
                print()
            else:
                print(f"[Post {counted_posts}] Swipe {swipes_done+1}/{swipes_needed} down", end="")

            # Perform a down swipe
            sx1, sy1, sx2, sy2, sdur, sdelay = random_swipe_down()
            swipes_done += 1

            # Reaction attempt – only on the first down swipe of a post
            if swipes_done == 1 and total_reactions < max_reactions and random.randint(1, 100) <= prob_percent:
                print(" | Trying reaction …", end="")
                forced = None
                if reaction_queue and total_reactions < len(reaction_queue):
                    forced = reaction_queue[total_reactions]
                if add_reaction(
                    base,
                    index,
                    serial,
                    screenshot_path,
                    like_template,
                    reaction_templates,
                    reaction_type,
                    total_reactions,
                    max_reactions,
                    (sx1 + sx2) // 2, (sy1 + sy2) // 2,  # approximate centre of swipe line as Like position
                    forced=forced,
                ):
                    total_reactions += 1
                    print(f" ✓ (total={total_reactions})")
                else:
                    print(" ✗")
            else:
                print()  # no reaction on this swipe

            do_swipe(base, index, sx1, sy1, sx2, sy2, sdur, sdelay, "[Swipe]")
            # Small extra pause to keep UI responsive
            time.sleep(0.5)

        elapsed = (time.time() - start) / 60
        print("\n=== FINISHED ===")
        print(f"Elapsed time: {elapsed:.1f}/{minutes} min")
        print(f"Posts processed: {counted_posts}")
        print(f"Reactions sent: {total_reactions}")

    --------------------------------------------------------------
    Argument parsing & entry point
    --------------------------------------------------------------
    def main():
        parser = argparse.ArgumentParser(
            description="LDPlayer + Facebook Newsfeed automation (launch, open FB, scroll & react)."
        )
        parser.add_argument("--ld-base", type=str, default=DEFAULT_LD_BASE,
                            help=f"Folder containing ldconsole.exe, ld.exe, adb.exe (default: {DEFAULT_LD_BASE})")
        parser.add_argument("--index", type=int, required=True,
                            help="LDPlayer instance index to use (e.g., 4 for 36.FB:Acc phụ)")
        parser.add_argument("--time", type=int, default=30,
                            help="How many minutes to run the newsfeed loop (default: 30)")
        parser.add_argument("--prob", type=int, default=10,
                            help="Chance (0‑100) to attempt a reaction on a post (default: 10)")
        parser.add_argument("--max-reactions", type=int, default=20,
                            help="Maximum number of reactions to send (default: 20)")
        parser.add_argument("--reaction-type", type=str, default="like,haha,love",
                            help=("Comma‑separated list of reactions to use, or the word 'random'. "
                                  "Example: like,love,haha,wow,sad,angry"))
        parser.add_argument("--template-dir", type=str, default=DEFAULT_TEMPLATE_DIR,
                            help=f"Folder containing reaction template images (default: {DEFAULT_TEMPLATE_DIR})")
        args = parser.parse_args()

        # Resolve paths
        ld_base = Path(args.ld_base).resolve()
        if not ld_base.is_dir():
            sys.exit(f"[ERROR] LDPlayer base folder not found: {ld_base}")

        template_dir = Path(args.template_dir).resolve()
        if not template_dir.is_dir():
            sys.exit(f"[ERROR] Template folder not found: {template_dir}")

        # Build template paths
        like_tpl = template_dir / "like.png"
        reaction_tpls = {
            "like": str(template_dir / "like.png"),
            "love": str(template_dir / "love.jpg"),
            "thuongthuong": str(template_dir / "thuongthuong.jpg"),
            "haha": str(template_dir / "haha.jpg"),
            "wow": str(template_dir / "wow.jpg"),
            "sad": str(template_dir / "sad.jpg"),
            "angry": str(template_dir / "angry.jpg"),
        }
        for name, path in reaction_tpls.items():
            if not os.path.isfile(path):
                print(f"[WARN] Missing template for {name}: {path}")

        if not os.path.isfile(like_tpl):
            sys.exit(f"[ERROR] Like template not found: {like_tpl}")

        # Prepare ADB serial
        serial = adb_serial(str(ld_base), args.index)
        screenshot_path = str(Path(file).with_name(f"fb_screen_{args.index}_{int(time.time())}.png"))

        # Optional: verify ADB connectivity
        print(f"[INFO] ADB serial for index {args.index}: {serial}")
        try:
            out = subprocess.run(
                [str(ld_base / ADB_EXE_NAME), "-s", serial, "get-state"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if out.stdout.strip() != "device":
                print("[WARN] Device not ready – attempting to connect via adb …")
                subprocess.run(
                    [str(ld_base / ADB_EXE_NAME), "connect", serial],
                    capture_output=True,
                    timeout=5,
                )
        except Exception as e:
            print(f"[WARN] ADB check failed: {e}")

        # Launch LDPlayer & open Facebook
        launch_instance(str(ld_base), args.index)
        open_facebook(str(ld_base), args.index)

        # Run the scrolling loop
        scroll_newsfeed(
            base=str(ld_base),
            index=args.index,
            serial=serial,
            screenshot_path=screenshot_path,
            like_template=str(like_tpl),
            reaction_templates=reaction_tpls,
            minutes=args.time,
            prob_percent=args.prob,
            max_reactions=args.max_reactions,
            reaction_type=args.reaction_type,
        )

    if name == "main":
        main()