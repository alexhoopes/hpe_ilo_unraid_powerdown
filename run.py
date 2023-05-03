import datetime
import json
import logging
import os
import paramiko
import redfish
import smtplib
import time

from email.message import EmailMessage
from dotenv import load_dotenv

# Init vars
load_dotenv()
ADMIN_EMAIL = os.getenv('ADMIN_EMAIL')
GMAIL_USERNAME = os.getenv('GMAIL_USERNAME')
GMAIL_PASSWORD = os.getenv('GMAIL_PASSWORD')
ILO_HOST = os.getenv('ILO_HOST')
ILO_USERNAME = os.getenv('ILO_USERNAME')
ILO_PASSWORD = os.getenv('ILO_PASSWORD')
UNRAID_HOST = os.getenv('UNRAID_HOST')
UNRAID_PASSWORD = os.getenv('UNRAID_PASSWORD')

current_date_time = datetime.datetime.now()
iml_event_time = ""
found_event = False

# Set up logging
log_filename = "logs/" + current_date_time.strftime("%Y-%m-%d") + ".log"
logging.basicConfig(filename=log_filename, level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logging.info('Checking iLO Integrated Management Log for powerloss events...')

# Create redfish object, login to iLO
REDFISH_OBJ = redfish.redfish_client(base_url=ILO_HOST,username=ILO_USERNAME, \
                        password=ILO_PASSWORD, default_prefix='/redfish/v1')
REDFISH_OBJ.login(auth="basic")

# Get current IML and count
iml = json.loads(REDFISH_OBJ.get('/redfish/v1/systems/1/logservices/iml/entries').text)
iml_count = iml['Members@odata.count']

# Check the last 3 events within the last minute
for i in range(iml_count-1, max(iml_count-4, -1), -1):
    iml_event = json.loads(REDFISH_OBJ.get(f'/redfish/v1/systems/1/logservices/iml/entries/{i}').text)
    iml_event_time = datetime.datetime.strptime(iml_event['Created'], '%Y-%m-%dT%H:%M:%SZ')
    time_diff = current_date_time - iml_event_time
    if time_diff.total_seconds() < 60 and 'Input Power Loss' in iml_event['Message']:
        found_event = True, i
        break
    elif time_diff.total_seconds() > 60:
        break

# If no new events exist, end
if not found_event:
    logging.info("No new events reported.")
    REDFISH_OBJ.logout()

# If new event found, wait for restoration then continue
if found_event:
    logging.error('System has suffered power loss, waiting 10 minutes to check for power restoration...')
    time.sleep(600)
    iml_event = json.loads(REDFISH_OBJ.get(f'/redfish/v1/systems/1/logservices/iml/entries/{found_event[1]}').text)
    if iml_event['Oem']['Hp']['Repaired'] == True:
        logging.info('System power recovered')
    else:
        logging.critical('System power not recovered - gracefully powering down server.')
        with paramiko.SSHClient() as ssh:
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(UNRAID_HOST, username='root', password=UNRAID_PASSWORD)
            ssh.exec_command('powerdown')
        shutdown_confirmed = False
        logging.info(f'Waiting for graceful powerdown...')
        
        while not shutdown_confirmed:
            try:
                with paramiko.SSHClient() as ssh_check:
                    ssh_check.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    ssh_check.connect(UNRAID_HOST, username='root', password=UNRAID_PASSWORD, timeout=5)
                    ssh_check.close()
            except:
                shutdown_confirmed = True
                logging.info('System successfully powered down. Sending email...')
                break
            time.sleep(5)
            
        # Setup email details
        msg = EmailMessage()
        msg.set_content(f"On {iml_event_time} Unraid server {UNRAID_HOST} suffered a powerloss event for more than 10 minutes and has been gracefully shut down to prevent data loss.")

        msg['Subject'] = 'Unraid Server Powerloss'
        msg['From'] = GMAIL_USERNAME
        msg['To'] = ADMIN_EMAIL
        try:
            mail_server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
            mail_server.ehlo()
            mail_server.login(GMAIL_USERNAME, GMAIL_PASSWORD)
            mail_server.send_message(msg)
            mail_server.close()
            logging.info(f'Email sent to {ADMIN_EMAIL}')
        except Exception as e:
            logging.error(f'Email failed to send: {e}')
    REDFISH_OBJ.logout()
