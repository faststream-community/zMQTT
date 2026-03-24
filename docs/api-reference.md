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
        - start
        - stop
        - get_message
        - __aenter__
        - __aexit__
        - __aiter__
        - __anext__

::: zmqtt.Message
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

::: zmqtt.QoS
    options:
      show_source: false

::: zmqtt.RetainHandling
    options:
      show_source: false

## Properties (MQTT 5.0)

::: zmqtt.PublishProperties
    options:
      show_source: false

::: zmqtt.ConnectProperties
    options:
      show_source: false

::: zmqtt.AuthProperties
    options:
      show_source: false

## Exceptions

::: zmqtt.MQTTError
    options:
      show_source: false

::: zmqtt.MQTTConnectError
    options:
      show_source: false

::: zmqtt.MQTTProtocolError
    options:
      show_source: false

::: zmqtt.MQTTDisconnectedError
    options:
      show_source: false

::: zmqtt.MQTTTimeoutError
    options:
      show_source: false
