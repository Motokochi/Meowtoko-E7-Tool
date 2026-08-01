# Match counting

`match_counting.py` owns the shared result cap and overflow guidance. The
compiled context retains the request ID, cap, and the stable filter IDs a user
can tighten when a search exceeds the cap.

Exact rows arrive in ascending Cartesian order. Match `N + 1` is the overflow
sentinel for a cap of `N`; no later event is consumed and no partial rows are
published.

The three-position category count remains a compatibility ABI. Production
searches populate only the exact position and keep the other two positions at
zero.
