"""The game engine: wall, state, events, actions, flow, agents, environment.

- jansou.game.wall: the shuffled sequence and its positional mapping
- jansou.game.state: authoritative game and per-player state
- jansou.game.events: the typed event stream and per-seat masking
- jansou.game.actions: action types and positional legality
- jansou.game.flow: turn and call flow, and round resolution
- jansou.game.progression: honba, rotation, ending conditions, ranking
- jansou.game.agents: the decision interface and the reference agents
- jansou.game.environment: the referee that runs a whole game
"""
