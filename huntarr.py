#!/usr/bin/env python3

import os
import sys
import time
import json
import subprocess
import logging
import re
import requests
from datetime import datetime
from typing import Tuple, Dict, Optional, List, Any

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("huntarr-tdarr")

###################################
# Configuration
###################################
class Config:
    def __init__(self):
        # Where your Tdarr Node log is located (Required)
        self.tdarr_node_log_path = os.environ.get("TDARR_NODE_LOG_PATH", "")
        
        # ------------- Tautulli (Required) -------------
        self.tautulli_api_key = os.environ.get("TAUTULLI_API_KEY", "")
        self.tautulli_url = os.environ.get("TAUTULLI_URL", "")

        # ----------- Tdarr Settings (Required) -------------
        self.tdarr_alter_workers_str = os.environ.get("TDARR_ALTER_WORKERS", "")
        self.tdarr_alter_workers = self.tdarr_alter_workers_str.lower() == "true" if self.tdarr_alter_workers_str else None
        
        # Parse TDARR_DEFAULT_LIMIT as required
        self.tdarr_default_limit_str = os.environ.get("TDARR_DEFAULT_LIMIT", "")
        try:
            self.tdarr_default_limit = int(self.tdarr_default_limit_str) if self.tdarr_default_limit_str else None
        except ValueError:
            self.tdarr_default_limit = None
            
        self.tdarr_api_url = os.environ.get("TDARR_API_URL", "")
        self.container_name = os.environ.get("CONTAINER_NAME", "tdarr_node")

        # ------------ Worker Scaling Settings ------------
        try:
            self.offset_threshold = int(os.environ.get("OFFSET_THRESHOLD", "1"))
        except ValueError:
            self.offset_threshold = 1
            logger.warning("Invalid OFFSET_THRESHOLD value, using default: 1")

        # ------------ Node Killer Settings ------------
        try:
            self.transcode_threshold = int(os.environ.get("TRANSCODE_THRESHOLD", "1"))
        except ValueError:
            self.transcode_threshold = 1
            logger.warning("Invalid TRANSCODE_THRESHOLD value, using default: 1")

        # ----------- Other -------------
        try:
            self.wait_seconds = int(os.environ.get("WAIT_SECONDS", "10"))
        except ValueError:
            self.wait_seconds = 10
            logger.warning("Invalid WAIT_SECONDS value, using default: 10")
            
        try:
            self.basic_check = int(os.environ.get("BASIC_CHECK", "3"))
        except ValueError:
            self.basic_check = 3
            logger.warning("Invalid BASIC_CHECK value, using default: 3")
            
        try:
            self.restart_delay = int(os.environ.get("RESTART_DELAY", "30"))
        except ValueError:
            self.restart_delay = 30
            logger.warning("Invalid RESTART_DELAY value, using default: 30")

        # Validate required configuration
        self.validate()

    def validate(self) -> None:
        """Validate that required configuration is provided"""
        errors = []

        # Required core variables
        if not self.tdarr_node_log_path:
            errors.append("TDARR_NODE_LOG_PATH is required")
            
        if self.tdarr_alter_workers is None:
            errors.append("TDARR_ALTER_WORKERS is required (must be 'true' or 'false')")
            
        if self.tdarr_default_limit is None:
            errors.append("TDARR_DEFAULT_LIMIT is required (must be a number)")
        
        # Required API variables
        if not self.tautulli_api_key:
            errors.append("TAUTULLI_API_KEY is required")
        
        if not self.tautulli_url:
            errors.append("TAUTULLI_URL is required")
        
        if not self.tdarr_api_url:
            errors.append("TDARR_API_URL is required")

        if errors:
            for error in errors:
                logger.error(error)
            sys.exit(1)

class HuntarrTdarr:
    def __init__(self, config: Config):
        self.config = config
        self.tdarr_node_id = ""
        self.total_count = 0
        self.last_operation = ""
        self.last_gpu_limit = 0
        self.consecutive_duplicates = 0

    def find_latest_node_id(self) -> str:
        """Find the latest nodeID from the Tdarr Node log file"""
        if not os.path.isfile(self.config.tdarr_node_log_path):
            return ""
        
        try:
            with open(self.config.tdarr_node_log_path, 'r') as f:
                content = f.read()
                matches = re.findall(r'"nodeID":\s*"([^"]+)"', content)
                if matches:
                    return matches[-1]
        except Exception as e:
            logger.error(f"Error reading node log file: {e}")
        
        return ""

    def ensure_node_id_loaded(self) -> bool:
        """Ensure that the nodeID is loaded"""
        if self.tdarr_node_id:
            return True
        
        logger.info(f"Attempting to retrieve nodeID from {self.config.tdarr_node_log_path}")
        node_id = self.find_latest_node_id()
        
        if not node_id:
            logger.error(f"Could not find any nodeID in {self.config.tdarr_node_log_path}")
            return False
        
        self.tdarr_node_id = node_id
        logger.info(f"Found nodeID: {self.tdarr_node_id}")
        return True

    def refresh_node_id_if_changed(self) -> None:
        """Check if the nodeID has changed and update it"""
        latest = self.find_latest_node_id()
        if not latest:
            logger.warning("Could not find any 'nodeID' lines in the log to refresh.")
            return
        
        if latest != self.tdarr_node_id:
            logger.info(f"NOTICE: nodeID changed from [{self.tdarr_node_id}] -> [{latest}]. Updating.")
            self.tdarr_node_id = latest
        else:
            logger.info(f"NOTICE: nodeID is still the same [{self.tdarr_node_id}].")

    def check_single_tautulli_connection(self, api_key: str, url: str) -> bool:
        """Check connection to a single Tautulli instance"""
        if not api_key or not url:
            return False
        
        logger.info(f"Checking Tautulli at: {url}")
        
        try:
            response = requests.get(f"{url}?apikey={api_key}&cmd=get_activity")
            response.raise_for_status()
            response.json()  # Validate JSON
            logger.info(f"Tautulli OK: {url}")
            return True
        except Exception as e:
            logger.warning(f"WARNING: Could not connect or invalid JSON: {url} - {e}")
            return False

    def check_tautulli_connections_on_startup(self) -> None:
        """Check all Tautulli connections on startup"""
        # T1 must work
        if not self.check_single_tautulli_connection(self.config.t1_tautulli_api_key, self.config.t1_tautulli_url):
            logger.error("ERROR: T1 not reachable. Exiting.")
            sys.exit(1)
        
        # T2..T4 are optional
        self.check_single_tautulli_connection(self.config.t2_tautulli_api_key, self.config.t2_tautulli_url)
        self.check_single_tautulli_connection(self.config.t3_tautulli_api_key, self.config.t3_tautulli_url)
        self.check_single_tautulli_connection(self.config.t4_tautulli_api_key, self.config.t4_tautulli_url)

    def fetch_transcode_counts_from_tautulli(self, api_key: str, url: str) -> Tuple[int, int]:
        """Fetch transcoding counts from a Tautulli instance"""
        if not api_key or not url:
            return (0, 0)
        
        try:
            response = requests.get(f"{url}?apikey={api_key}&cmd=get_activity")
            response.raise_for_status()
            data = response.json()
            
            # Only count sessions that are transcoding video, not just audio
            local_count = 0
            remote_count = 0
            
            if (data and 'response' in data and 'data' in data['response'] and 
                'sessions' in data['response']['data']):
                
                for session in data['response']['data']['sessions']:
                    if (session.get('transcode_decision') == 'transcode' and 
                        session.get('video_decision') == 'transcode'):
                        
                        if session.get('ip_address', '').startswith('10.0.0.'):
                            local_count += 1
                        else:
                            remote_count += 1
            
            return (local_count, remote_count)
        except Exception as e:
            logger.warning(f"Error fetching Tautulli data: {e}")
            return (0, 0)

    def get_total_watchers(self) -> int:
        """Get total number of transcoding watchers from all Tautulli instances"""
        total = 0
        
        # T1 is required
        t1_local, t1_remote = self.fetch_transcode_counts_from_tautulli(
            self.config.t1_tautulli_api_key, self.config.t1_tautulli_url
        )
        total += t1_local + t1_remote
        
        # Optional T2
        if self.config.t2_tautulli_api_key and self.config.t2_tautulli_url:
            t2_local, t2_remote = self.fetch_transcode_counts_from_tautulli(
                self.config.t2_tautulli_api_key, self.config.t2_tautulli_url
            )
            total += t2_local + t2_remote
        
        # Optional T3
        if self.config.t3_tautulli_api_key and self.config.t3_tautulli_url:
            t3_local, t3_remote = self.fetch_transcode_counts_from_tautulli(
                self.config.t3_tautulli_api_key, self.config.t3_tautulli_url
            )
            total += t3_local + t3_remote
        
        # Optional T4
        if self.config.t4_tautulli_api_key and self.config.t4_tautulli_url:
            t4_local, t4_remote = self.fetch_transcode_counts_from_tautulli(
                self.config.t4_tautulli_api_key, self.config.t4_tautulli_url
            )
            total += t4_local + t4_remote
        
        return total

    def is_plex_transcoding_over_threshold(self) -> bool:
        """Check if Plex transcoding is over the threshold"""
        logger.info("Checking Plex transcodes...")

        total_local = 0
        total_remote = 0

        # T1
        t1_local, t1_remote = self.fetch_transcode_counts_from_tautulli(
            self.config.t1_tautulli_api_key, self.config.t1_tautulli_url
        )
        total_local += t1_local
        total_remote += t1_remote

        # T2
        t2_local, t2_remote = self.fetch_transcode_counts_from_tautulli(
            self.config.t2_tautulli_api_key, self.config.t2_tautulli_url
        )
        total_local += t2_local
        total_remote += t2_remote

        # T3
        t3_local, t3_remote = self.fetch_transcode_counts_from_tautulli(
            self.config.t3_tautulli_api_key, self.config.t3_tautulli_url
        )
        total_local += t3_local
        total_remote += t3_remote

        # T4
        t4_local, t4_remote = self.fetch_transcode_counts_from_tautulli(
            self.config.t4_tautulli_api_key, self.config.t4_tautulli_url
        )
        total_local += t4_local
        total_remote += t4_remote

        self.total_count = total_local + total_remote

        logger.info(f"Found {total_local} local & {total_remote} remote => total={self.total_count}, threshold={self.config.transcode_threshold}")

        # Return True if watchers >= threshold
        return self.total_count >= self.config.transcode_threshold

    def is_container_running(self) -> bool:
        """Check if the Tdarr container is running"""
        try:
            result = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", self.config.container_name],
                capture_output=True,
                text=True,
                check=False
            )
            return result.stdout.strip() == "true"
        except Exception as e:
            logger.error(f"Error checking container status: {e}")
            return False

    def adjust_tdarr_workers(self, watchers: int) -> None:
        """Adjust the number of Tdarr GPU workers based on transcoding load"""
        if not self.ensure_node_id_loaded():
            return

        # Calculate how many watchers are above the offset
        watchers_over_offset = 0
        if self.config.offset_threshold == 0:
            watchers_over_offset = watchers
        else:
            watchers_over_offset = watchers - self.config.offset_threshold + 1
            if watchers_over_offset < 0:
                watchers_over_offset = 0

        # Desired = TDARR_DEFAULT_LIMIT - watchersOverOffset
        desired = self.config.tdarr_default_limit - watchers_over_offset
        if desired < 0:
            desired = 0

        logger.info(f"watchers={watchers} => watchersOverOffset={watchers_over_offset} => desiredWorkers={desired}")

        # Poll current worker limits
        try:
            response = requests.post(
                f"{self.config.tdarr_api_url}/api/v2/poll-worker-limits",
                headers={"Content-Type": "application/json"},
                json={"data": {"nodeID": self.tdarr_node_id}}
            )
            response.raise_for_status()
            data = response.json()
            
            current = data.get('workerLimits', {}).get('transcodegpu')
            if current is None:
                logger.error(f"ERROR: Could not retrieve current GPU worker limit for nodeID='{self.tdarr_node_id}'. Will re-check log for a new ID.")
                self.refresh_node_id_if_changed()
                return
            
            logger.info(f"Current GPU worker limit: {current}")
            
            diff = desired - current
            if diff == 0:
                logger.info(f"Already at the desired GPU worker limit ({desired}).")
                return
            
            if diff > 0:
                step = "increase"
                logger.info(f"Need to increase by {diff}")
                
                # Modified increase branch using real Tautulli watcher count
                logger.info("Need to increase workers. Initiating delay...")
                original_watchers = self.get_total_watchers()
                
                initial_watchers_over_offset = 0
                if self.config.offset_threshold == 0:
                    initial_watchers_over_offset = original_watchers
                else:
                    initial_watchers_over_offset = original_watchers - self.config.offset_threshold + 1
                    if initial_watchers_over_offset < 0:
                        initial_watchers_over_offset = 0
                
                initial_desired = self.config.tdarr_default_limit - initial_watchers_over_offset
                if initial_desired < 0:
                    initial_desired = 0
                
                logger.info(f"Before delay: Tautulli watchers={original_watchers}, initial desired workers={initial_desired}")
                
                delay = self.config.restart_delay
                interval = 5
                elapsed = 0
                
                while elapsed < delay:
                    time.sleep(interval)
                    elapsed += interval
                    
                    during_watchers = self.get_total_watchers()
                    during_watchers_over_offset = 0
                    
                    if self.config.offset_threshold == 0:
                        during_watchers_over_offset = during_watchers
                    else:
                        during_watchers_over_offset = during_watchers - self.config.offset_threshold + 1
                        if during_watchers_over_offset < 0:
                            during_watchers_over_offset = 0
                    
                    current_desired = self.config.tdarr_default_limit - during_watchers_over_offset
                    if current_desired < 0:
                        current_desired = 0
                    
                    logger.info(f"During delay: Tautulli watchers={during_watchers}, desired workers={current_desired} (initial desired was {initial_desired})")
                    
                    if current_desired < initial_desired:
                        logger.info(f"Desired workers dropped from {initial_desired} to {current_desired} during delay. Cancelling worker increase.")
                        return
                
                # Final confirmation: get final desired from Tautulli
                final_watchers = self.get_total_watchers()
                final_watchers_over_offset = 0
                
                if self.config.offset_threshold == 0:
                    final_watchers_over_offset = final_watchers
                else:
                    final_watchers_over_offset = final_watchers - self.config.offset_threshold + 1
                    if final_watchers_over_offset < 0:
                        final_watchers_over_offset = 0
                
                final_desired = self.config.tdarr_default_limit - final_watchers_over_offset
                if final_desired < 0:
                    final_desired = 0
                
                # Get current GPU worker limit again
                response = requests.post(
                    f"{self.config.tdarr_api_url}/api/v2/poll-worker-limits",
                    headers={"Content-Type": "application/json"},
                    json={"data": {"nodeID": self.tdarr_node_id}}
                )
                response.raise_for_status()
                new_data = response.json()
                new_current = new_data.get('workerLimits', {}).get('transcodegpu', 0)
                
                logger.info(f"Final confirmation: desired workers={final_desired}, current workers={new_current}")
                
                if final_desired <= new_current:
                    logger.info("No longer need to increase workers after final confirmation. Cancelling the increase.")
                    return
                
                # Apply the changes
                for i in range(final_desired - new_current):
                    requests.post(
                        f"{self.config.tdarr_api_url}/api/v2/alter-worker-limit",
                        headers={"Content-Type": "application/json"},
                        json={"data": {"nodeID": self.tdarr_node_id, "process": "increase", "workerType": "transcodegpu"}}
                    )
                    time.sleep(1)
                
                logger.info("GPU worker limit adjustment complete.")
            else:
                # Decrease workers
                step = "decrease"
                diff = -diff
                logger.info(f"Need to decrease by {diff}")
                
                for i in range(diff):
                    requests.post(
                        f"{self.config.tdarr_api_url}/api/v2/alter-worker-limit",
                        headers={"Content-Type": "application/json"},
                        json={"data": {"nodeID": self.tdarr_node_id, "process": "decrease", "workerType": "transcodegpu"}}
                    )
                    time.sleep(1)
                
                logger.info("GPU worker limit adjustment complete.")
                
        except Exception as e:
            logger.error(f"Error adjusting Tdarr workers: {e}")

    def set_initial_gpu_workers(self) -> None:
        """Set initial GPU workers on startup"""
        if not self.config.tdarr_alter_workers:
            return
            
        logger.info(f"Setting initial GPU workers to default limit: {self.config.tdarr_default_limit} on startup")
        
        if not self.ensure_node_id_loaded():
            logger.error("ERROR: Could not get nodeID, can't set initial GPU workers")
            time.sleep(5)
            return
        
        try:
            response = requests.post(
                f"{self.config.tdarr_api_url}/api/v2/poll-worker-limits",
                headers={"Content-Type": "application/json"},
                json={"data": {"nodeID": self.tdarr_node_id}}
            )
            response.raise_for_status()
            data = response.json()
            
            current_limit = data.get('workerLimits', {}).get('transcodegpu')
            if current_limit is not None:
                diff = self.config.tdarr_default_limit - current_limit
                
                if diff != 0:
                    step = ""
                    count = 0
                    
                    if diff > 0:
                        step = "increase"
                        count = diff
                        logger.info(f"Need to increase by {diff} to reach default limit")
                    else:
                        step = "decrease"
                        count = -diff
                        logger.info(f"Need to decrease by {-diff} to reach default limit")
                    
                    for i in range(count):
                        requests.post(
                            f"{self.config.tdarr_api_url}/api/v2/alter-worker-limit",
                            headers={"Content-Type": "application/json"},
                            json={"data": {"nodeID": self.tdarr_node_id, "process": step, "workerType": "transcodegpu"}}
                        )
                        time.sleep(1)
                    
                    logger.info(f"Initial GPU worker limit set to {self.config.tdarr_default_limit}")
                else:
                    logger.info(f"GPU workers already at desired default limit: {current_limit}")
            else:
                logger.error("ERROR: Could not get current GPU worker limit")
        except Exception as e:
            logger.error(f"Error setting initial GPU workers: {e}")

    def run(self) -> None:
        """Main execution loop"""
        # Initial setup
        self.ensure_node_id_loaded()
        
        # Check Tautulli connection
        if not self.check_tautulli_connection():
            logger.error("ERROR: Tautulli not reachable. Exiting.")
            sys.exit(1)
            
        self.set_initial_gpu_workers()
        
        # Main loop
        while True:
            try:
                if self.is_plex_transcoding_over_threshold():
                    # Transcoding is over threshold
                    if self.config.tdarr_alter_workers:
                        # Adjust workers mode
                        operation = f"reduce_workers_{self.total_count}"
                        
                        try:
                            response = requests.post(
                                f"{self.config.tdarr_api_url}/api/v2/poll-worker-limits",
                                headers={"Content-Type": "application/json"},
                                json={"data": {"nodeID": self.tdarr_node_id}}
                            )
                            response.raise_for_status()
                            data = response.json()
                            current_limit = data.get('workerLimits', {}).get('transcodegpu', 0)
                            
                            if operation == self.last_operation and current_limit == self.last_gpu_limit:
                                self.consecutive_duplicates += 1
                                if self.consecutive_duplicates > 2:
                                    logger.info(f"Skipping duplicate worker adjustment (done {self.consecutive_duplicates} times already)")
                                    time.sleep(self.config.wait_seconds)
                                    continue
                            else:
                                self.consecutive_duplicates = 0
                            
                            self.last_operation = operation
                            self.last_gpu_limit = current_limit
                            
                            logger.info("Threshold exceeded. Reducing GPU workers.")
                            self.adjust_tdarr_workers(self.total_count)
                            time.sleep(self.config.wait_seconds)
                            
                        except Exception as e:
                            logger.error(f"Error in worker adjustment: {e}")
                            time.sleep(self.config.wait_seconds)
                    else:
                        # Kill container mode
                        operation = "kill_container"
                        
                        if operation == self.last_operation:
                            self.consecutive_duplicates += 1
                            if self.consecutive_duplicates > 2:
                                logger.info(f"Skipping duplicate container management (done {self.consecutive_duplicates} times already)")
                                time.sleep(self.config.wait_seconds)
                                continue
                        else:
                            self.consecutive_duplicates = 0
                        
                        self.last_operation = operation
                        
                        if self.is_container_running():
                            logger.info(f"Threshold exceeded: Killing {self.config.container_name}")
                            subprocess.run(["docker", "kill", self.config.container_name], check=False)
                        else:
                            logger.info(f"{self.config.container_name} is already stopped.")
                        
                        time.sleep(self.config.wait_seconds)
                else:
                    # Below threshold
                    if self.config.tdarr_alter_workers:
                        # Adjust workers mode
                        operation = f"adjust_workers_{self.total_count}"
                        
                        try:
                            response = requests.post(
                                f"{self.config.tdarr_api_url}/api/v2/poll-worker-limits",
                                headers={"Content-Type": "application/json"},
                                json={"data": {"nodeID": self.tdarr_node_id}}
                            )
                            response.raise_for_status()
                            data = response.json()
                            current_limit = data.get('workerLimits', {}).get('transcodegpu', 0)
                            
                            if operation == self.last_operation and current_limit == self.last_gpu_limit:
                                self.consecutive_duplicates += 1
                                if self.consecutive_duplicates > 2:
                                    logger.info(f"Skipping duplicate worker adjustment (done {self.consecutive_duplicates} times already)")
                                    time.sleep(self.config.basic_check)
                                    continue
                            else:
                                self.consecutive_duplicates = 0
                            
                            self.last_operation = operation
                            self.last_gpu_limit = current_limit
                            
                            self.adjust_tdarr_workers(self.total_count)
                        
                        except Exception as e:
                            logger.error(f"Error in worker adjustment: {e}")
                    
                    # Check container state regardless of mode
                    operation = "start_container"
                    
                    if operation == self.last_operation and self.is_container_running():
                        self.consecutive_duplicates += 1
                        if self.consecutive_duplicates > 2:
                            logger.info(f"Skipping duplicate container check (done {self.consecutive_duplicates} times already)")
                            time.sleep(self.config.basic_check)
                            continue
                    else:
                        self.consecutive_duplicates = 0
                    
                    self.last_operation = operation
                    
                    if not self.is_container_running():
                        # In node kill mode, wait RESTART_DELAY seconds before starting container
                        if not self.config.tdarr_alter_workers:
                            logger.info(f"Below threshold in node kill mode -> Waiting {self.config.restart_delay} seconds before starting container {self.config.container_name}.")
                            initial_watchers = self.get_total_watchers()
                            delay = self.config.restart_delay
                            interval = 5
                            elapsed = 0
                            
                            continue_outer_loop = False
                            while elapsed < delay and not continue_outer_loop:
                                time.sleep(interval)
                                elapsed += interval
                                current_watchers = self.get_total_watchers()
                                
                                if current_watchers >= self.config.transcode_threshold:
                                    logger.info(f"Watcher count increased to {current_watchers} during delay. Skipping container start.")
                                    continue_outer_loop = True
                            
                            if continue_outer_loop:
                                continue
                        
                        logger.info(f"Below threshold -> Starting container {self.config.container_name}.")
                        subprocess.run(["docker", "start", self.config.container_name], check=False)
                    else:
                        logger.info(f"Container {self.config.container_name} is already running.")
                    
                    time.sleep(self.config.basic_check)
            
            except Exception as e:
                logger.error(f"Unexpected error in main loop: {e}")
                time.sleep(self.config.basic_check) = current_limit
                            
                            self.adjust_tdarr_workers(self.total_count)
                        
                        except Exception as e:
                            logger.error(f"Error in worker adjustment: {e}")
                    
                    # Check container state regardless of mode
                    operation = "start_container"
                    
                    if operation == self.last_operation and self.is_container_running():
                        self.consecutive_duplicates += 1
                        if self.consecutive_duplicates > 2:
                            logger.info(f"Skipping duplicate container check (done {self.consecutive_duplicates} times already)")
                            time.sleep(self.config.basic_check)
                            continue
                    else:
                        self.consecutive_duplicates = 0
                    
                    self.last_operation = operation
                    
                    if not self.is_container_running():
                        # In node kill mode, wait RESTART_DELAY seconds before starting container
                        if not self.config.tdarr_alter_workers:
                            logger.info(f"Below threshold in node kill mode -> Waiting {self.config.restart_delay} seconds before starting container {self.config.container_name}.")
                            initial_watchers = self.get_total_watchers()
                            delay = self.config.restart_delay
                            interval = 5
                            elapsed = 0
                            
                            continue_outer_loop = False
                            while elapsed < delay and not continue_outer_loop:
                                time.sleep(interval)
                                elapsed += interval
                                current_watchers = self.get_total_watchers()
                                
                                if current_watchers >= self.config.transcode_threshold:
                                    logger.info(f"Watcher count increased to {current_watchers} during delay. Skipping container start.")
                                    continue_outer_loop = True
                            
                            if continue_outer_loop:
                                continue
                        
                        logger.info(f"Below threshold -> Starting container {self.config.container_name}.")
                        subprocess.run(["docker", "start", self.config.container_name], check=False)
                    else:
                        logger.info(f"Container {self.config.container_name} is already running.")
                    
                    time.sleep(self.config.basic_check)
            
            except Exception as e:
                logger.error(f"Unexpected error in main loop: {e}")
                time.sleep(self.config.basic_check)

if __name__ == "__main__":
    try:
        config = Config()
        huntarr = HuntarrTdarr(config)
        huntarr.run()
    except KeyboardInterrupt:
        logger.info("Process interrupted. Exiting.")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Critical error: {e}")
        sys.exit(1)