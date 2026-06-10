# Trajectory Class Diagram

```mermaid
classDiagram
    class Trajectory {
        +path: Path
        +md_text: str
        +meta: dict
        +raw_json: dict
        +is_success: bool
        +skill_used: str | None
        +skill_generated: str | None
        +canary_side: str | None
        +status: str | None
        -_registry: Registry | None
        +load(path, registry) Trajectory
    }

    class TrajectoryValidation {
        +valid: bool
        +reason: str | None
        +detail: str
        +user_intent_count: int
    }

    class TrajectoryHit {
        +trajectory: Trajectory
        +similarity: float
    }

    class SkillHit {
        +skill: Skill
        +similarity: float
    }

    class Registry {
        +trajectory_status(traj_path) dict | None
        +trajectories_using(skill_name) list[Path]
    }

    class WatchDir {
        +id: int
        +path: Path
        +label: str
        +auto_index: bool
        +traj_count: int
        +indexed_count: int
        +ecosystem: str
    }

    class Candidate {
        +pattern: str
        +kind: str
        +attach_to: str | None
        +supporting_trajs: list[str]
        +first_seen: date | None
        +promoted: bool
    }

    class UxScoreResult {
        +scored: bool
        +score: int | None
        +reasons: str
        +decision: dict
    }

    class FileThreePiece {
        <<package>>
    }

    class TrajectoryRow {
        <<DB table>>
        +id: int
        +watch_dir_id: int
        +filename: str
        +has_meta: bool
        +has_embedding: bool
        +status: str
        +process_action: str
        +skill_generated: str
        +skill_used: str
        +canary_side: str
        +source_model: str
        +source_harness: str
        +ux_score: float
        +error_msg: str | None
        +retry_count: int
    }

    class Status {
        <<enum>>
        discovered
        indexed
        promoted
    }

    Trajectory --> Registry : uses
    Trajectory --> TrajectoryRow : DB row
    Trajectory ..> "traj_<id>.md" : reads
    Trajectory ..> "traj_<id>.md.meta" : reads
    Trajectory ..> "traj_<id>.json" : reads
    TrajectoryHit --> Trajectory
    TrajectoryRow --> WatchDir : FK
    TrajectoryRow --> Status
```

Paste this at **[mermaid.live](https://mermaid.live)** → the diagram renders live.
