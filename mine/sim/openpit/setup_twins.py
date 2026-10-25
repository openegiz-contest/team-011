"""Create or update the pit twins in Ditto (idempotent PUTs).

    python -m openpit.setup_twins
"""

import logging
import sys

from .bus import Ditto
from .dispatch import FixedDispatcher
from .model import MineModel
from .params import Params
from .twins import definitions


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    model = MineModel(Params(), seed=0, dispatcher=FixedDispatcher({}))
    ditto = Ditto()
    things = definitions(model)
    for thing_id, body in things.items():
        status = ditto.put_thing(thing_id, body)
        print(f"{'created' if status == 201 else 'updated'}  {thing_id}")
    print(f"{len(things)} twins ready in {ditto.url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
