#!/usr/bin/env python3
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Reading files that live in somebody else's cloud.

Every source this repository harvests from sits behind a syncing daemon --
iCloud Drive for the archive, Google Drive File Stream for the AI Studio
exports -- and both hand back an error rather than a file when they are busy
materialising it or when another process holds it. DEVONthink indexing the same
folder is the usual other process.

These are *transient*. The file is fine and the caller is fine; the read simply
has to happen again a moment later. Treating them as ordinary failures is what
turned a single unreadable file into a lost run, twice, in two different
scripts:

  OSError:    [Errno 11] Resource deadlock avoided   -- iCloud, oss-harvest.py
  TimeoutError: [Errno 60] Operation timed out       -- Google Drive, aistudio-extract.py

Both aborted a stage that had already done all its fetching. So this lives in
one place now, rather than being learned separately by each script that reads a
synced folder.
"""

import errno
import time

# The codes a syncing daemon returns for "not right now".
#
# EDEADLK and EBUSY come from iCloud; ETIMEDOUT from Google Drive File Stream,
# which blocks on a network fetch and gives up; EAGAIN is the generic form. A
# code outside this set -- a permission problem, a file that is genuinely gone --
# is a real failure and must not be retried into a silent fallback.
TRANSIENT = {errno.EDEADLK, errno.EBUSY, errno.EAGAIN, errno.ETIMEDOUT}


def read_text_resilient(path, retries=4, fallback="", quiet=False):
    """Read a synced file, retrying briefly while the daemon is busy with it.

    Gives up on THAT FILE and returns ``fallback`` rather than raising, because
    the callers are harvesters: losing one note is a cosmetic loss, and losing
    the run that had already fetched everything is not.

    Anything that is not a sync-contention error is raised as normal.
    """
    delay = 0.25
    for attempt in range(retries):
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            if e.errno not in TRANSIENT:
                raise
            if attempt == retries - 1:
                if not quiet:
                    print(f"    ! unreadable after {retries} tries ({path.name}): {e}", flush=True)
                return fallback
            time.sleep(delay)
            delay *= 2
    return fallback
