"""Module map shown on the About tab and in the report: which syllabus topic lives where."""

MODULES = {
    "os": [
        {"id": "OS-1", "name": "CPU scheduling", "file": "engine/scheduler.py",
         "idea": "Every intersection is a CPU and every lane is a process. FCFS, Round Robin, SRTF with aging, "
                 "Priority with aging and MLFQ decide which lane gets the green. A switch costs one all-red tick "
                 "(context-switch overhead).",
         "see": "Command centre > Scheduler, OS lab > Policy arena",
         "viva": "Why does SRTF starve long queues? How does aging fix it? What is the cost of a context switch here?"},
        {"id": "OS-2", "name": "Process synchronisation", "file": "engine/sync.py",
         "idea": "Roads are counting semaphores, intersection boxes are mutexes, gate threads and the simulation loop "
                 "form a producer/consumer pair over a bounded buffer, and a readers-writer lock protects shared state.",
         "see": "OS lab > Synchronisation",
         "viva": "Where is the critical section? How is starvation of the writer avoided in the RW lock?"},
        {"id": "OS-3", "name": "Deadlock handling", "file": "engine/deadlock.py",
         "idea": "Gridlock is a real deadlock (hold and wait, circular wait). The system can ignore it, detect it with "
                 "a wait-for graph and recover by towing a victim, or avoid it with a safe-state check. "
                 "The Banker's algorithm is implemented in full.",
         "see": "OS lab > Deadlock",
         "viva": "Name the four Coffman conditions in this model. How is the victim chosen?"},
        {"id": "OS-4", "name": "Memory management", "file": "engine/memory.py",
         "idea": "The downtown garage is physical memory and each parking bay is a frame. A permit that is not "
                 "resident causes a page fault and a victim is evicted by FIFO, LRU, LFU or Clock. "
                 "Belady's anomaly is demonstrated.",
         "see": "OS lab > Memory",
         "viva": "Why is OPT impossible in practice? When does Clock beat FIFO?"},
    ],
    "dbms": [
        {"id": "DB-1", "name": "Schema design and integrity", "file": "db/schema.sql",
         "idea": "Sixteen tables in 3NF with primary keys, foreign keys, CHECK, UNIQUE and NOT NULL constraints.",
         "see": "DB lab > Schema",
         "viva": "Why is owner separate from vehicle? Which constraints enforce consistency?"},
        {"id": "DB-2", "name": "Transactions and ACID", "file": "db/transactions.py",
         "idea": "Paying a fine updates wallet, treasury and fine together. Failures are injected at each step to show "
                 "atomic rollback; SAVEPOINTs let one failed fine roll back without losing the others.",
         "see": "DB lab > Transactions",
         "viva": "Which ACID property does the CHECK constraint protect? What is a savepoint?"},
        {"id": "DB-3", "name": "Concurrency control", "file": "db/concurrency.py",
         "idea": "A conflict-serialisability tester (precedence graph), a strict 2PL lock manager with deadlock "
                 "detection, and a real multi-threaded lost-update experiment.",
         "see": "DB lab > Concurrency",
         "viva": "Is 2PL enough to avoid deadlock? Draw the precedence graph of a non-serialisable schedule."},
        {"id": "DB-4", "name": "Indexing and query optimisation", "file": "db/optimizer.py",
         "idea": "200,000 readings queried with no index, a composite B-tree index and a covering index, with "
                 "EXPLAIN QUERY PLAN and measured timings.",
         "see": "DB lab > Indexing",
         "viva": "Why does column order matter in a composite index? What makes an index covering?"},
        {"id": "DB-5", "name": "Triggers and views", "file": "db/schema.sql",
         "idea": "A trigger turns every violation into an escalating fine, other triggers audit money movements and "
                 "protect paid fines. Views feed the analytics with window functions.",
         "see": "DB lab > Triggers, Analytics",
         "viva": "What is the difference between BEFORE and AFTER triggers? Why use a view?"},
        {"id": "DB-6", "name": "Logging and crash recovery", "file": "db/recovery.py",
         "idea": "A write-ahead log with a steal/no-force buffer pool, and an ARIES-style recovery: analysis, "
                 "redo and undo with compensation records. SQLite's own WAL mode runs the live database.",
         "see": "DB lab > Recovery",
         "viva": "Why must the log be flushed before the data page? What is a CLR?"},
    ],
}
