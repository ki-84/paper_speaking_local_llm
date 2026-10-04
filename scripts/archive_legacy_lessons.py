"""Archive display cards predating the first overview/deep-dive project."""

import argparse
import json

from paperspeak.video_library import archive_before_story

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Hide the listed cards; no files or recordings are deleted",
    )
    args = parser.parse_args()
    print(
        json.dumps(archive_before_story(apply=args.apply), ensure_ascii=False, indent=2)
    )
