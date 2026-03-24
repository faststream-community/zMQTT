# CHANGELOG


## v0.0.1-alpha.4 (2026-03-23)

### Chores

- Extend linting ([#13](https://github.com/faststream-community/zMQTT/pull/13),
  [`df3dde7`](https://github.com/faststream-community/zMQTT/commit/df3dde720c05b74c556670cd84e182273a61b071))

### Features

- Add windows and macos tests ([#12](https://github.com/faststream-community/zMQTT/pull/12),
  [`9854447`](https://github.com/faststream-community/zMQTT/commit/9854447a5102e1010524e16640fa76d35d1518f9))

- Queue limit ([#6](https://github.com/faststream-community/zMQTT/pull/6),
  [`b30be27`](https://github.com/faststream-community/zMQTT/commit/b30be2777b8d199168606fca5b3300f54bdd600f))


## v0.0.1-alpha.3 (2026-03-22)

### Chores

- Add 3.10 python compat
  ([`6a08554`](https://github.com/faststream-community/zMQTT/commit/6a08554fdef91e3a8a1edb3d43cf0aa1c6d1fb51))

- Add python 3.10 support ([#10](https://github.com/faststream-community/zMQTT/pull/10),
  [`5bfb461`](https://github.com/faststream-community/zMQTT/commit/5bfb461b67bdd4791a3744003062e3f924d7390a))

### Documentation

- Clarify subscribe routing and fix MQTT5 auth wording
  ([`343280a`](https://github.com/faststream-community/zMQTT/commit/343280a40a75c588b002e9c4931df1d59b2684bb))

- Clarify subscribe routing and fix MQTT5 auth wording
  ([#9](https://github.com/faststream-community/zMQTT/pull/9),
  [`34adbef`](https://github.com/faststream-community/zMQTT/commit/34adbef398dd35bbe3133b111435d42949e943fd))


## v0.0.1-alpha.2 (2026-03-21)

### Features

- Rename to zmqtt
  ([`4098df6`](https://github.com/faststream-community/zMQTT/commit/4098df6a175f00f1c5deb3b903c10c9ec7373f8b))

- Rename to zmqtt ([#8](https://github.com/faststream-community/zMQTT/pull/8),
  [`301b037`](https://github.com/faststream-community/zMQTT/commit/301b0378710de925c284767fdb8e53fa91c9cbc5))


## v0.0.1-alpha.1 (2026-03-21)

### Chores

- Add release pipeline
  ([`071b85d`](https://github.com/faststream-community/zMQTT/commit/071b85d167c6b26a8dd0c85bc08f4ea65f86958c))

chore: Add release pipeline

- Add release pipeline
  ([`77ae34f`](https://github.com/faststream-community/zMQTT/commit/77ae34f6da88f108d8547b61892a1af064cd09bb))

- Tests infra
  ([`8f3b14c`](https://github.com/faststre                                                                        
50 +                                                                                                                                                       
51 +    Attributes:                                                                                                                                        
52 +        enabled: Whether to reconnect automatically. Set to ``False`` to let                                                                           
53 +            exceptions propagate immediately on disconnection.                                                                                         
54 +        initial_delay: Seconds to wait before the first reconnection attempt.                                                                          
55 +        max_delay: Upper bound (seconds) for the exponential back-off delay.                                                                           
56 +        backoff_factor: Multiplier applied to the delay after each failed attempt. am-community/zMQTT/commit/8f3b14cdc9eb2bd4d8c4f74404bc26232d6dc525))
