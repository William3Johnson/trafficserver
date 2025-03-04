"""
Verify traffic_dump HTTP/3 functionality.
"""
#  Licensed to the Apache Software Foundation (ASF) under one
#  or more contributor license agreements.  See the NOTICE file
#  distributed with this work for additional information
#  regarding copyright ownership.  The ASF licenses this file
#  to you under the Apache License, Version 2.0 (the
#  "License"); you may not use this file except in compliance
#  with the License.  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

import os
import sys

Test.Summary = '''
Verify traffic_dump HTTP/3 functionality.
'''

Test.SkipUnless(
    Condition.PluginExists('traffic_dump.so'),
    Condition.HasATSFeature('TS_USE_QUIC'),
)
Test.SkipIf(
    Condition.true("Skip this test until the TS_EVENT_HTTP_SSN are supported for QUIC connections."),
)

schema_path = os.path.join(Test.Variables.AtsTestToolsDir, 'lib', 'replay_schema.json')

# Configure the origin server.
replay_file = "replay/http3.yaml"
server = Test.MakeVerifierServerProcess(
    "server", replay_file,
    ssl_cert="ssl/server_combined.pem", ca_cert="ssl/signer.pem")


# Define ATS and configure it.
ts = Test.MakeATSProcess("ts", enable_tls=True, enable_quic=True)
ts_log_dir = os.path.join(ts.RunDirectory, "ts", "log")
qlog_dir = os.path.join(ts_log_dir, "qlog_dir")

ts.addSSLfile("ssl/server.pem")
ts.addSSLfile("ssl/server.key")
ts.addSSLfile("ssl/signer.pem")

ts.Disk.records_config.update({
    'proxy.config.diags.debug.enabled': 1,
    'proxy.config.diags.debug.tags': 'traffic_dump|quic',
    'proxy.config.http.insert_age_in_response': 0,

    'proxy.config.quic.qlog_dir': qlog_dir,

    'proxy.config.ssl.server.cert.path': ts.Variables.SSLDir,
    'proxy.config.ssl.server.private_key.path': ts.Variables.SSLDir,
    'proxy.config.url_remap.pristine_host_hdr': 1,
    'proxy.config.ssl.CA.cert.filename': f'{ts.Variables.SSLDir}/signer.pem',
    'proxy.config.exec_thread.autoconfig.scale': 1.0,
    'proxy.config.http.host_sni_policy': 2,
    'proxy.config.ssl.client.verify.server.policy': 'PERMISSIVE',
})

ts.Disk.ssl_multicert_config.AddLine(
    'dest_ip=* ssl_cert_name=server.pem ssl_key_name=server.key'
)

ts.Disk.remap_config.AddLine(
    f'map https://www.client_only_tls.com/ http://127.0.0.1:{server.Variables.http_port}'
)
ts.Disk.remap_config.AddLine(
    f'map https://www.tls.com/ https://127.0.0.1:{server.Variables.https_port}'
)
ts.Disk.remap_config.AddLine(
    f'map / http://127.0.0.1:{server.Variables.http_port}'
)

# Configure traffic_dump.
ts.Disk.plugin_config.AddLine(
    f'traffic_dump.so --logdir {ts_log_dir} --sample 1 --limit 1000000000 '
    '--sensitive-fields "cookie,set-cookie,x-request-1,x-request-2"'
)
# Configure logging of transactions. This is helpful for the cache test below.
ts.Disk.logging_yaml.AddLines(
    '''
logging:
  formats:
    - name: basic
      format: "%<cluc>: Read result: %<crc>:%<crsc>:%<chm>, Write result: %<cwr>"
  logs:
    - filename: transactions
      format: basic
'''.split('\n'))

# Set up trafficserver expectations.
ts.Disk.diags_log.Content = Testers.ContainsExpression(
    "loading plugin.*traffic_dump.so",
    "Verify the traffic_dump plugin got loaded.")
ts.Disk.traffic_out.Content = Testers.ContainsExpression(
    f"Initialized with log directory: {ts_log_dir}",
    "Verify traffic_dump initialized with the configured directory.")
ts.Disk.traffic_out.Content += Testers.ContainsExpression(
    "Initialized with sample pool size 1 bytes and disk limit 1000000000 bytes",
    "Verify traffic_dump initialized with the configured disk limit.")
ts.Disk.traffic_out.Content += Testers.ContainsExpression(
    "Finish a session with log file of.*bytes",
    "Verify traffic_dump sees the end of sessions and accounts for it.")

# Set up the json replay file expectations.
replay_file_session_1 = os.path.join(ts_log_dir, "127", "0000000000000000")
ts.Disk.File(replay_file_session_1, exists=True)

# Execute the first transaction. We limit the threads to 1 so that the sessions
# are run in serial.
tr = Test.AddTestRun("Run the test traffic.")
tr.AddVerifierClientProcess(
    "client", replay_file, http_ports=[ts.Variables.port],
    https_ports=[ts.Variables.ssl_port],
    http3_ports=[ts.Variables.ssl_port],
    ssl_cert="ssl/server_combined.pem", ca_cert="ssl/signer.pem",
    other_args='--thread-limit 1')

tr.Processes.Default.StartBefore(server)
tr.Processes.Default.StartBefore(ts)
tr.StillRunningAfter = server
tr.StillRunningAfter = ts

#
# Test 1: Verify the correct behavior of two transactions across two sessions.
#

# Verify the properties of the replay file for the first transaction.
tr = Test.AddTestRun("Verify the json content of the first session")
http_protocols = "tcp,ip"
verify_replay = "verify_replay.py"
sensitive_fields_arg = (
    "--sensitive-fields cookie "
    "--sensitive-fields set-cookie "
    "--sensitive-fields x-request-1 "
    "--sensitive-fields x-request-2 ")
tr.Setup.CopyAs(verify_replay, Test.RunDirectory)
tr.Processes.Default.Command = \
    (f'{sys.executable} {verify_replay} {schema_path} {replay_file_session_1} '
     f'{sensitive_fields_arg} --client-http-version "3" '
     f'--client-protocols "{http_protocols}"')
tr.Processes.Default.ReturnCode = 0
tr.StillRunningAfter = server
tr.StillRunningAfter = ts
