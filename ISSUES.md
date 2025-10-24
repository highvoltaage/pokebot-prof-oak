# Repo Issue Tracker

_This file is auto-generated. Do not edit by hand._

## Issue #3 — Leaf Green Unown tracker
State: open
Labels: bug, fr/lg

### Description
despite catching multiple shiny unown in the first ruin in the ruins of alph, the shiny quota for that route does not update
---

## Issue #4 — Sapphire, Emerald, Ruby - shiny quota
State: open
Labels: bug, Ruby/Saph, emerald

### Description
the shiny quota does not automatically update whenever a shiny is caught. instead, you have to restart the game and enable the module whether it's the prof oak or living prof oak module in order for the shiny quota to update
---

## Issue #5 — Sapphire, Emerald, Ruby - auto nav #1
State: open
Labels: bug, Ruby/Saph, emerald

### Description
even if shiny quota is met on the route you are, the auto nav skips the route ahead and heads to the one after. if the other route has an npc blocking the path due to missing story pieces, the bot breaks. after resetting, the bot begins to freeze and will only stop freezing and crashing after closing the command prompt window and open the instance again. one example is route 101, 102 and 103. if you have to battle your rival, in route 103 the auto nav will attempt to navigate to route 102 but the npc is blocking the path due to unfinished story (battling your rival and getting the pokedex). the same goes for from route 101 to route 103, once finished with route 101 the auto nav attempts to go to route 102 (naturally 2 is after 1) but again npc is blocking the path so you have to manually go to route 103 to finish that route. after finishing 102, auto nav attempts to go to the next route but npc is blocking the way.
---

## Issue #6 — Sapphire, Emerald, Ruby - auto nav #2
State: open
Labels: bug, Ruby/Saph, emerald

### Description
similar to [Sapphire, Emerald, Ruby - auto nav #1](https://github.com/highvoltaage/pokebot-prof-oak/issues/5) after the path to 102 is clear, if you start either modules and the quota is met for 101, the bot goes to 102, spins for a moment (if you have spin enabled), runs away (if you have run away enabled) goes to 103, quota is also met, attempts to go to 104 but npc blocks the path. manual mode is required to navigate back to 102 and start the module in order to continue the route before continuing
---

