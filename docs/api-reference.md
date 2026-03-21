# API Reference

## Factory

::: zmqtt.client.create_client
    options:
      show_source: false

## Client protocols

::: zmqtt.client.MQTTClientV311
    options:
      show_source: false

::: zmqtt.client.MQTTClientV5
    options:
      show_source: false

## Core classes

::: zmqtt.client.MQTTClient
    options:
      show_source: false

::: zmqtt.client.Subscription
    options:
      show_source: false
      members:
        - get_message
        - __aenter__
        - __aexit__
        - __aiter__
        - __anext__

::: zmqtt.types.Message
    options:
      show_source: false
      members:
        - topic
        - payload
        - qos
        - retain
        - properties
        - ack

## Configuration

::: zmqtt.client.ReconnectConfig
    options:
      show_source: false

## Enumerations

::: zmqtt.types.QoS
    options:
      show_source: false

::: zmqtt.types.RetainHandling
    options:
      show_source: false

## Properties (MQTT 5.0)

::: zmqtt.packets.properties.PublishProperties
    options:
      show_source: false

::: zmqtt.packets.properties.ConnectProperties
    options:
      show_source: false

::: zmqtt.packets.properties.AuthProperties
    options:
      show_source: false

## Exceptions

::: zmqtt.errors.MQTTError
    options:
      show_source: false

::: zmqtt.errors.MQTTConnectError
    options:
      show_source: false

::: zmqtt.errors.MQTTProtocolError
    options:
      show_source: false

::: zmqtt.errors.MQTTDisconnectedError
    options:
      show_source: false

::: zmqtt.errors.MQTTTimeoutError
    options:
      show_source: false
