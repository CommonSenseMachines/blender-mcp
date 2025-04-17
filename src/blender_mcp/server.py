# blender_mcp_server.py
from mcp.server.fastmcp import FastMCP, Context, Image
import socket
import json
import asyncio
import logging
from dataclasses import dataclass
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Any, List
import os
import requests
import time

# Configure logging
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("BlenderMCPServer")

@dataclass
class BlenderConnection:
    host: str
    port: int
    sock: socket.socket = None  # Changed from 'socket' to 'sock' to avoid naming conflict
    
    def connect(self) -> bool:
        """Connect to the Blender addon socket server"""
        if self.sock:
            return True
            
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            logger.info(f"Connected to Blender at {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Blender: {str(e)}")
            self.sock = None
            return False
    
    def disconnect(self):
        """Disconnect from the Blender addon"""
        if self.sock:
            try:
                self.sock.close()
            except Exception as e:
                logger.error(f"Error disconnecting from Blender: {str(e)}")
            finally:
                self.sock = None

    def receive_full_response(self, sock, buffer_size=8192):
        """Receive the complete response, potentially in multiple chunks"""
        chunks = []
        # Remove timeout to wait indefinitely like in test_animation.py
        sock.settimeout(None)
        
        try:
            while True:
                try:
                    chunk = sock.recv(buffer_size)
                    if not chunk:
                        # If we get an empty chunk, the connection might be closed
                        if not chunks:  # If we haven't received anything yet, this is an error
                            raise Exception("Connection closed before receiving any data")
                        break
                    
                    chunks.append(chunk)
                    
                    # Check if we've received a complete JSON object
                    try:
                        data = b''.join(chunks)
                        json.loads(data.decode('utf-8'))
                        # If we get here, it parsed successfully
                        logger.info(f"Received complete response ({len(data)} bytes)")
                        return data
                    except json.JSONDecodeError:
                        # Incomplete JSON, continue receiving
                        continue
                except socket.timeout:
                    # If we hit a timeout during receiving, break the loop and try to use what we have
                    logger.warning("Socket timeout during chunked receive")
                    break
                except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
                    logger.error(f"Socket connection error during receive: {str(e)}")
                    raise  # Re-raise to be handled by the caller
        except socket.timeout:
            logger.warning("Socket timeout during chunked receive")
        except Exception as e:
            logger.error(f"Error during receive: {str(e)}")
            raise
            
        # If we get here, we either timed out or broke out of the loop
        # Try to use what we have
        if chunks:
            data = b''.join(chunks)
            logger.info(f"Returning data after receive completion ({len(data)} bytes)")
            try:
                # Try to parse what we have
                json.loads(data.decode('utf-8'))
                return data
            except json.JSONDecodeError:
                # If we can't parse it, it's incomplete
                raise Exception("Incomplete JSON response received")
        else:
            raise Exception("No data received")

    def send_command(self, command_type: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Send a command to Blender and return the response"""
        if not self.sock and not self.connect():
            raise ConnectionError("Not connected to Blender")
        
        command = {
            "type": command_type,
            "params": params or {}
        }
        
        try:
            # Log the command being sent
            logger.info(f"Sending command: {command_type} with params: {params}")
            
            # Send the command
            self.sock.sendall(json.dumps(command).encode('utf-8'))
            logger.info(f"Command sent, waiting for response...")
            
            # IMPORTANT: Don't set a timeout - this will allow it to wait indefinitely like test_animation.py
            # The socket already has a timeout set at connect time, but for long-running operations like animation,
            # we want to wait as long as needed for the complete response.
            self.sock.settimeout(None)
            
            # Receive the response using the improved receive_full_response method
            response_data = self.receive_full_response(self.sock)
            logger.info(f"Received {len(response_data)} bytes of data")
            
            response = json.loads(response_data.decode('utf-8'))
            logger.info(f"Response parsed, status: {response.get('status', 'unknown')}")
            
            if response.get("status") == "error":
                logger.error(f"Blender error: {response.get('message')}")
                raise Exception(response.get("message", "Unknown error from Blender"))
            
            return response.get("result", {})
        except socket.timeout:
            logger.error("Socket timeout while waiting for response from Blender")
            # Don't try to reconnect here - let the get_blender_connection handle reconnection
            # Just invalidate the current socket so it will be recreated next time
            self.sock = None
            raise Exception("Timeout waiting for Blender response - animation processing may take longer than expected. Check Blender for results.")
        except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
            logger.error(f"Socket connection error: {str(e)}")
            self.sock = None
            raise Exception(f"Connection to Blender lost: {str(e)}")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response from Blender: {str(e)}")
            # Try to log what was received
            if 'response_data' in locals() and response_data:
                logger.error(f"Raw response (first 200 bytes): {response_data[:200]}")
            raise Exception(f"Invalid response from Blender: {str(e)}")
        except Exception as e:
            logger.error(f"Error communicating with Blender: {str(e)}")
            # Don't try to reconnect here - let the get_blender_connection handle reconnection
            self.sock = None
            raise Exception(f"Communication error with Blender: {str(e)}")

@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    """Manage server startup and shutdown lifecycle"""
    # We don't need to create a connection here since we're using the global connection
    # for resources and tools
    
    try:
        # Just log that we're starting up
        logger.info("BlenderMCP server starting up")
        
        # Try to connect to Blender on startup to verify it's available
        try:
            # This will initialize the global connection if needed
            blender = get_blender_connection()
            logger.info("Successfully connected to Blender on startup")
        except Exception as e:
            logger.warning(f"Could not connect to Blender on startup: {str(e)}")
            logger.warning("Make sure the Blender addon is running before using Blender resources or tools")
        
        # Return an empty context - we're using the global connection
        yield {}
    finally:
        # Clean up the global connection on shutdown
        global _blender_connection
        if _blender_connection:
            logger.info("Disconnecting from Blender on shutdown")
            _blender_connection.disconnect()
            _blender_connection = None
        logger.info("BlenderMCP server shut down")

# Create the MCP server with lifespan support
mcp = FastMCP(
    "BlenderMCP",
    description="Blender integration through the Model Context Protocol",
    lifespan=server_lifespan
)

# Resource endpoints

# Global connection for resources (since resources can't access context)
_blender_connection = None
_csm_enabled = False  # Add this global variable

def get_blender_connection():
    """Get or create a persistent Blender connection"""
    global _blender_connection, _csm_enabled
    
    # If we have an existing connection, check if it's still valid
    if _blender_connection is not None:
        try:
            # Check if CSM.ai is enabled
            result = _blender_connection.send_command("get_csm_status")
            _csm_enabled = result.get("enabled", False)
            
            return _blender_connection
        except Exception as e:
            # Connection is dead, close it and create a new one
            logger.warning(f"Existing connection is no longer valid: {str(e)}")
            try:
                _blender_connection.disconnect()
            except:
                pass
            _blender_connection = None
    
    # Create a new connection if needed
    if _blender_connection is None:
        _blender_connection = BlenderConnection(host="localhost", port=9876)
        if not _blender_connection.connect():
            logger.error("Failed to connect to Blender")
            _blender_connection = None
            raise Exception("Could not connect to Blender. Make sure the Blender addon is running.")
        logger.info("Created new persistent connection to Blender")
        
        # Check integrations status
        try:
            result = _blender_connection.send_command("get_csm_status")
            _csm_enabled = result.get("enabled", False)
        except Exception as e:
            logger.warning(f"Failed to check integration status: {str(e)}")
    
    return _blender_connection


@mcp.tool()
def get_scene_info(ctx: Context) -> str:
    """Get detailed information about the current Blender scene"""
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_scene_info")
        
        # Just return the JSON representation of what Blender sent us
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting scene info from Blender: {str(e)}")
        return f"Error getting scene info: {str(e)}"

@mcp.tool()
def get_object_info(ctx: Context, object_name: str) -> str:
    """
    Get detailed information about a specific object in the Blender scene.
    
    Parameters:
    - object_name: The name of the object to get information about
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_object_info", {"name": object_name})
        
        # Just return the JSON representation of what Blender sent us
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting object info from Blender: {str(e)}")
        return f"Error getting object info: {str(e)}"



@mcp.tool()
def create_object(
    ctx: Context,
    type: str = "CUBE",
    name: str = None,
    location: List[float] = None,
    rotation: List[float] = None,
    scale: List[float] = None,
    # Torus-specific parameters
    align: str = "WORLD",
    major_segments: int = 48,
    minor_segments: int = 12,
    mode: str = "MAJOR_MINOR",
    major_radius: float = 1.0,
    minor_radius: float = 0.25,
    abso_major_rad: float = 1.25,
    abso_minor_rad: float = 0.75,
    generate_uvs: bool = True
) -> str:
    """
    Create a new object in the Blender scene.
    
    Parameters:
    - type: Object type (CUBE, SPHERE, CYLINDER, PLANE, CONE, TORUS, EMPTY, CAMERA, LIGHT)
    - name: Optional name for the object
    - location: Optional [x, y, z] location coordinates
    - rotation: Optional [x, y, z] rotation in radians
    - scale: Optional [x, y, z] scale factors (not used for TORUS)
    
    Torus-specific parameters (only used when type == "TORUS"):
    - align: How to align the torus ('WORLD', 'VIEW', or 'CURSOR')
    - major_segments: Number of segments for the main ring
    - minor_segments: Number of segments for the cross-section
    - mode: Dimension mode ('MAJOR_MINOR' or 'EXT_INT')
    - major_radius: Radius from the origin to the center of the cross sections
    - minor_radius: Radius of the torus' cross section
    - abso_major_rad: Total exterior radius of the torus
    - abso_minor_rad: Total interior radius of the torus
    - generate_uvs: Whether to generate a default UV map
    
    Returns:
    A message indicating the created object name.
    """
    try:
        # Get the global connection
        blender = get_blender_connection()
        
        # Set default values for missing parameters
        loc = location or [0, 0, 0]
        rot = rotation or [0, 0, 0]
        sc = scale or [1, 1, 1]
        
        params = {
            "type": type,
            "location": loc,
            "rotation": rot,
        }
        
        if name:
            params["name"] = name

        if type == "TORUS":
            # For torus, the scale is not used.
            params.update({
                "align": align,
                "major_segments": major_segments,
                "minor_segments": minor_segments,
                "mode": mode,
                "major_radius": major_radius,
                "minor_radius": minor_radius,
                "abso_major_rad": abso_major_rad,
                "abso_minor_rad": abso_minor_rad,
                "generate_uvs": generate_uvs
            })
            result = blender.send_command("create_object", params)
            return f"Created {type} object: {result['name']}"
        else:
            # For non-torus objects, include scale
            params["scale"] = sc
            result = blender.send_command("create_object", params)
            return f"Created {type} object: {result['name']}"
    except Exception as e:
        logger.error(f"Error creating object: {str(e)}")
        return f"Error creating object: {str(e)}"


@mcp.tool()
def modify_object(
    ctx: Context,
    name: str,
    location: List[float] = None,
    rotation: List[float] = None,
    scale: List[float] = None,
    visible: bool = None
) -> str:
    """
    Modify an existing object in the Blender scene.
    
    Parameters:
    - name: Name of the object to modify
    - location: Optional [x, y, z] location coordinates
    - rotation: Optional [x, y, z] rotation in radians
    - scale: Optional [x, y, z] scale factors
    - visible: Optional boolean to set visibility
    """
    try:
        # Get the global connection
        blender = get_blender_connection()
        
        params = {"name": name}
        
        if location is not None:
            params["location"] = location
        if rotation is not None:
            params["rotation"] = rotation
        if scale is not None:
            params["scale"] = scale
        if visible is not None:
            params["visible"] = visible
            
        result = blender.send_command("modify_object", params)
        return f"Modified object: {result['name']}"
    except Exception as e:
        logger.error(f"Error modifying object: {str(e)}")
        return f"Error modifying object: {str(e)}"

@mcp.tool()
def delete_object(ctx: Context, name: str) -> str:
    """
    Delete an object from the Blender scene.
    
    Parameters:
    - name: Name of the object to delete
    """
    try:
        # Get the global connection
        blender = get_blender_connection()
        
        result = blender.send_command("delete_object", {"name": name})
        return f"Deleted object: {name}"
    except Exception as e:
        logger.error(f"Error deleting object: {str(e)}")
        return f"Error deleting object: {str(e)}"

@mcp.tool()
def animate_object(ctx: Context, object_name: str, animation_prompt: str, temp_format: str = "glb", 
                 handle_original: str = "hide", collection_name: str = None) -> str:
    """
    Animate a 3D model using AI-generated animation based on a text prompt.
    
    Parameters:
    - object_name: Name of the object to animate
    - animation_prompt: Text description of the desired animation (e.g., "walking", "dancing")
    - temp_format: Format for temporary mesh export (default: "glb")
    - handle_original: How to handle the original object ("keep", "hide", "delete")
    - collection_name: Name of collection to organize animations (if None, creates "{object_name}_Animations")
    
    Returns information about the animated object.
    Note: Animation processing may take 30-60 seconds or more.
    """
    try:
        # Get the global connection
        blender = get_blender_connection()
        
        # First check if the object exists
        try:
            blender.send_command("get_object_info", {"name": object_name})
        except Exception as e:
            return json.dumps({
                "status": "error",
                "message": f"Object '{object_name}' not found: {str(e)}"
            }, indent=2)
        
        # Let the user know this might take some time
        logger.info(f"Starting animation process for '{object_name}' with prompt '{animation_prompt}'")
        logger.info("Animation processing may take 30-60 seconds or longer depending on model complexity")
        
        # Handle None value for collection_name properly
        collection_name_arg = f'"{collection_name}"' if collection_name else "None"
        
        # Create code to execute in Blender
        code = f"""
import bpy
import os
import tempfile
import base64
import requests
import json
import time
from pathlib import Path

# Animation server URL
SERVER_URL = "http://35.190.131.188:9000/animate"

def animate_mesh(obj_name, text_prompt, temp_format="glb", handle_original="hide", collection_name=None):
    print(f"[DEBUG] Animation process started at: {time.strftime('%H:%M:%S')}")
    print(f"[DEBUG] Starting animation of {{obj_name}} with prompt '{{text_prompt}}'")
    print(f"[DEBUG] Parameters: format={temp_format}, handle_original={handle_original}, collection={collection_name}")
    print("This may take 30-60 seconds or longer depending on model complexity")
    
    # Get the object
    obj = bpy.data.objects.get(obj_name)
    if not obj:
        print(f"[ERROR] Object {{obj_name}} not found in scene")
        return {{"status": "error", "message": f"Object {{obj_name}} not found"}}
    
    print(f"[DEBUG] Object found: {{obj.name}}, type: {{obj.type}}")
    
    if obj.type != 'MESH':
        print(f"[ERROR] Object {{obj_name}} is not a mesh (type: {{obj.type}})")
        return {{"status": "error", "message": f"Object {{obj_name}} is not a mesh"}}
    
    # Get Blender file directory
    blend_dir = os.path.dirname(bpy.data.filepath) if bpy.data.filepath else None
    print(f"[DEBUG] Blender file directory: {{blend_dir}}")
    
    # Create temp directory
    with tempfile.TemporaryDirectory() as temp_dir:
        print(f"[DEBUG] Created temp directory: {{temp_dir}}")
        
        # Create temporary filenames
        temp_mesh_path = os.path.join(temp_dir, f"{{obj_name}}_temp.{{temp_format}}")
        
        # Clean prompt for output filename
        safe_prompt = text_prompt.replace(" ", "_").replace("/", "-").lower()
        output_fbx_path = os.path.join(temp_dir, f"{{obj_name}}_{{safe_prompt}}.fbx")
        
        print(f"[DEBUG] Temp mesh path: {{temp_mesh_path}}")
        print(f"[DEBUG] Output FBX path: {{output_fbx_path}}")
        
        # Select only this object
        print(f"[DEBUG] Selecting object for export")
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        
        # Export the mesh
        print(f"[DEBUG] Exporting mesh to {{temp_format}} format")
        try:
            if temp_format == "glb":
                bpy.ops.export_scene.gltf(
                    filepath=temp_mesh_path,
                    export_format='GLB',
                    use_selection=True,
                    export_animations=False
                )
                print(f"[DEBUG] GLB export completed")
            else:
                # Fallback to FBX
                bpy.ops.export_scene.fbx(
                    filepath=temp_mesh_path,
                    use_selection=True,
                    embed_textures=True
                )
                print(f"[DEBUG] FBX export completed")
        except Exception as export_err:
            print(f"[ERROR] Export failed: {{str(export_err)}}")
            return {{"status": "error", "message": f"Failed to export mesh: {{str(export_err)}}"}}
        
        # Check if file exists
        if not os.path.exists(temp_mesh_path):
            print(f"[ERROR] Exported file not found at {{temp_mesh_path}}")
            return {{"status": "error", "message": f"Failed to export temporary mesh file"}}
        
        # Read and encode the mesh file
        file_size = os.path.getsize(temp_mesh_path)
        print(f"[DEBUG] Reading exported file ({{file_size}} bytes)")
        try:
            with open(temp_mesh_path, "rb") as f:
                mesh_b64 = base64.b64encode(f.read()).decode("utf-8")
            print(f"[DEBUG] File encoded to base64 (length: {{len(mesh_b64)}})")
        except Exception as encode_err:
            print(f"[ERROR] File encoding failed: {{str(encode_err)}}")
            return {{"status": "error", "message": f"Failed to encode mesh file: {{str(encode_err)}}"}}
        
        # Build the request payload
        print(f"[DEBUG] Building API request payload")
        payload = {{
            "mesh_b64_json": mesh_b64,
            "text_prompt": text_prompt,
            "is_gs": False,
            "opacity_threshold": 0.0,
            "no_fingers": False,
            "rest_pose_type": None,
            "ignore_pose_parts": [],
            "input_normal": False,
            "bw_fix": True,
            "bw_vis_bone": "LeftArm",
            "reset_to_rest": False,
            "retarget": True,
            "inplace": True
        }}
        
        # Send request and stream response to file
        print(f"[DEBUG] Sending animation request to AI service: {{SERVER_URL}}")
        request_start_time = time.time()
        try:
            # Set a longer timeout for the animation API request
            print(f"[DEBUG] Initiating POST request with 120s timeout")
            resp = requests.post(SERVER_URL, json=payload, stream=True, timeout=120)
            
            print(f"[DEBUG] Received response with status code: {{resp.status_code}}")
            if resp.status_code != 200:
                error_text = resp.text[:500] if resp.text else "No error details"
                print(f"[ERROR] Animation server returned error: {{resp.status_code}}, details: {{error_text}}")
                return {{
                    "status": "error",
                    "message": f"Animation server error: {{resp.status_code}}",
                    "details": error_text
                }}
            
            print("[DEBUG] Receiving animation data from AI service...")
            # Save the animated FBX file
            bytes_received = 0
            with open(output_fbx_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        bytes_received += len(chunk)
                        f.write(chunk)
            
            request_duration = time.time() - request_start_time
            print(f"[DEBUG] Download complete in {{request_duration:.2f}} seconds, received {{bytes_received}} bytes")
            
            if not os.path.exists(output_fbx_path):
                print(f"[ERROR] FBX file not saved at {{output_fbx_path}}")
                return {{"status": "error", "message": "Failed to save animated FBX file"}}
            
            fbx_size = os.path.getsize(output_fbx_path)
            print(f"[DEBUG] Animation received! FBX file size: {{fbx_size}} bytes")
            print(f"[DEBUG] Importing animated FBX into Blender...")
            
            # Import the animated file
            # First store current objects to determine which are new
            existing_objects = set(bpy.data.objects)
            
            # Import the animated FBX
            try:
                bpy.ops.import_scene.fbx(filepath=output_fbx_path)
                print(f"[DEBUG] FBX import operation completed")
            except Exception as import_err:
                print(f"[ERROR] FBX import failed: {{str(import_err)}}")
                return {{"status": "error", "message": f"Failed to import animated FBX: {{str(import_err)}}"}}
            
            # Get new objects
            imported_objects = list(set(bpy.data.objects) - existing_objects)
            print(f"[DEBUG] Import created {{len(imported_objects)}} new objects")
            
            if not imported_objects:
                print(f"[ERROR] No objects were imported from the animation FBX")
                return {{"status": "error", "message": "No objects imported from animation"}}
            
            # Get the imported armature and mesh
            armature_obj = None
            mesh_obj = None
            
            for new_obj in imported_objects:
                print(f"[DEBUG] Imported object: {{new_obj.name}}, type: {{new_obj.type}}")
                if new_obj.type == 'ARMATURE':
                    armature_obj = new_obj
                    new_obj.name = f"{{obj_name}}_{{safe_prompt}}_armature"
                    print(f"[DEBUG] Renamed armature to: {{new_obj.name}}")
                elif new_obj.type == 'MESH':
                    mesh_obj = new_obj
                    new_obj.name = f"{{obj_name}}_{{safe_prompt}}"
                    print(f"[DEBUG] Renamed mesh to: {{new_obj.name}}")
            
            # Organize them in a new collection
            if collection_name is None:
                collection_name = f"{{obj_name}}_Animations"
                
            print(f"[DEBUG] Using collection: {{collection_name}}")
            if collection_name not in bpy.data.collections:
                print(f"[DEBUG] Creating new collection: {{collection_name}}")
                anim_collection = bpy.data.collections.new(collection_name)
                bpy.context.scene.collection.children.link(anim_collection)
            else:
                print(f"[DEBUG] Using existing collection: {{collection_name}}")
                anim_collection = bpy.data.collections[collection_name]
                
            # Move objects to the animation collection
            print(f"[DEBUG] Moving objects to collection: {{collection_name}}")
            for new_obj in imported_objects:
                # First remove from current collections
                for coll in list(new_obj.users_collection):
                    print(f"[DEBUG] Removing {{new_obj.name}} from collection {{coll.name}}")
                    coll.objects.unlink(new_obj)
                
                # Add to animation collection
                print(f"[DEBUG] Adding {{new_obj.name}} to collection {{anim_collection.name}}")
                anim_collection.objects.link(new_obj)
            
            # Position the animated model at the same location as the original
            if armature_obj and obj:
                print(f"[DEBUG] Setting armature location to match original: {{obj.location}}")
                armature_obj.location = obj.location.copy()
            
            # Handle the original object based on the handle_original parameter
            print(f"[DEBUG] Handling original object with mode: {{handle_original}}")
            if handle_original == "hide":
                # Hide the original but keep it
                print(f"[DEBUG] Hiding original object")
                obj.hide_viewport = True
                obj.hide_render = True
            elif handle_original == "delete":
                # Delete the original object
                print(f"[DEBUG] Deleting original object")
                bpy.data.objects.remove(obj)
            elif handle_original == "keep":
                # Keep the original as is, but move it to the side
                print(f"[DEBUG] Moving original object to the side")
                obj.location.x += 3.0
            
            # Clean up any potential backup meshes or collections that might have been created
            print(f"[DEBUG] Checking for backup collections and objects to clean up")
            # Check if MCP_Backup_Meshes collection exists and remove it
            backup_coll = bpy.data.collections.get("MCP_Backup_Meshes")
            if backup_coll:
                print(f"[DEBUG] Found backup collection to remove: {{backup_coll.name}}")
                # First remove any objects in this collection
                for backup_obj in list(backup_coll.objects):
                    print(f"[DEBUG] Removing backup object: {{backup_obj.name}}")
                    bpy.data.objects.remove(backup_obj)
                # Then remove the collection
                print(f"[DEBUG] Removing backup collection")
                bpy.data.collections.remove(backup_coll)
            
            # Check for backup objects with _backup suffix
            backup_obj_name = f"{{obj_name}}_backup"
            if backup_obj_name in bpy.data.objects:
                print(f"[DEBUG] Removing backup object: {{backup_obj_name}}")
                bpy.data.objects.remove(bpy.data.objects[backup_obj_name])
            
            print(f"[DEBUG] Animation complete! Created {{len(imported_objects)}} objects in collection {{collection_name}}")
            
            # Return the result
            result = {{
                "status": "success",
                "message": f"Animation created for {{obj_name}} with prompt '{{text_prompt}}'",
                "imported_objects": [o.name for o in imported_objects],
                "collection": collection_name,
                "original_object": obj_name,
                "handle_original": handle_original
            }}
            
            if armature_obj:
                result["armature"] = armature_obj.name
            if mesh_obj:
                result["mesh"] = mesh_obj.name
                
            print(f"[DEBUG] Animation process completed at: {time.strftime('%H:%M:%S')}")
            return result
            
        except requests.exceptions.Timeout:
            print(f"[ERROR] Animation request timed out after 120 seconds")
            return {{
                "status": "error", 
                "message": "Animation service request timed out after 120 seconds. The service might be busy or the model is too complex."
            }}
        except Exception as e:
            import traceback
            print(f"[ERROR] Exception during animation processing:")
            traceback.print_exc()
            return {{"status": "error", "message": str(e)}}

# Run the animation function
print(f"[DEBUG] Starting animation script with: object={object_name}, prompt='{animation_prompt}'")
result = animate_mesh("{object_name}", "{animation_prompt}", "{temp_format}", "{handle_original}", {collection_name_arg})
print(f"[DEBUG] Animation result: {{json.dumps(result)}}")
result
"""
        
        # Execute the code in Blender
        try:
            result = blender.send_command("execute_code", {"code": code})
            
            # Parse the result
            if isinstance(result, dict) and "result" in result:
                if isinstance(result["result"], dict):
                    return json.dumps(result["result"], indent=2)
                else:
                    return str(result["result"])
            
            return json.dumps(result, indent=2)
            
        except socket.timeout:
            # Handle timeout specifically
            logger.error("Timeout while waiting for animation processing")
            return json.dumps({
                "status": "timeout",
                "message": "The animation request is taking longer than expected. Check Blender to see if animation completed.",
                "recovery_steps": [
                    "The animation may still be processing in Blender",
                    "Check the Blender UI for imported objects or new collections",
                    "If nothing appears after 2-3 minutes, try again with a simpler animation prompt"
                ]
            }, indent=2)
        
    except Exception as e:
        logger.error(f"Error animating object: {str(e)}")
        return f"Error animating object: {str(e)}"

@mcp.tool()
def set_material(
    ctx: Context,
    object_name: str,
    material_name: str = None,
    color: List[float] = None
) -> str:
    """
    Set or create a material for an object.
    
    Parameters:
    - object_name: Name of the object to apply the material to
    - material_name: Optional name of the material to use or create
    - color: Optional [R, G, B] color values (0.0-1.0)
    """
    try:
        # Get the global connection
        blender = get_blender_connection()
        
        params = {"object_name": object_name}
        
        if material_name:
            params["material_name"] = material_name
        if color:
            params["color"] = color
            
        result = blender.send_command("set_material", params)
        return f"Applied material to {object_name}: {result.get('material_name', 'unknown')}"
    except Exception as e:
        logger.error(f"Error setting material: {str(e)}")
        return f"Error setting material: {str(e)}"

@mcp.tool()
def execute_blender_code(ctx: Context, code: str) -> str:
    """
    Execute arbitrary Python code in Blender.
    
    Parameters:
    - code: The Python code to execute
    """
    try:
        # Get the global connection
        blender = get_blender_connection()
        
        result = blender.send_command("execute_code", {"code": code})
        return f"Code executed successfully: {result.get('result', '')}"
    except Exception as e:
        logger.error(f"Error executing code: {str(e)}")
        return f"Error executing code: {str(e)}"

@mcp.prompt()
def asset_creation_strategy() -> str:
    """Defines the preferred strategy for creating assets in Blender"""
    return """When creating 3D content in Blender, always start by checking if integrations are available:

    0. Before anything, always check the scene from get_scene_info()
    1. CSM.ai is good at generating (via search) 3D models for single item very quickly.
    Use get_csm_status() to verify its status
    If CSM.ai is enabled:
    - For objects/models, do the following steps:
        1. Search for existing 3D models using search_csm_models() with a descriptive text query
        2. Review the available models and select the most appropriate one
        3. Import the selected model using import_csm_model() with the model's ID and GLB URL
        4. After importing the model, ALWAYS check the world_bounding_box and adjust the mesh's location, scale, and rotation

    2. If CSM integrations are disabled or when falling back to basic tools:
       - create_object() for basic primitives (CUBE, SPHERE, CYLINDER, etc.)
       - set_material() for basic colors and materials
    
    3. When including an object into scene, ALWAYS make sure that the name of the object is meanful.

    4. Always check the world_bounding_box for each item so that:
        - Ensure that all objects that should not be clipping are not clipping.
        - Items have right spatial relationship.
    
    5. After giving the tool location/scale/rotation information (via create_object() and modify_object()),
       double check the related object's location, scale, rotation, and world_bounding_box using get_object_info(),
       so that the object is in the desired location.
       
    6. To add animation to a mesh object:
       - Use animate_object() with the name of the object and a descriptive prompt
       - Animation prompts can be actions like "walking", "dancing", "jumping", etc.
       - Only mesh objects can be animated
       - The animation will create a new animated version of the object
       - The original object may be modified or hidden by the animation process

    Recommended workflow for creating assets:
    1. First try CSM.ai for finding existing high-quality 3D models
    2. For animation, use the animate_object() function with descriptive prompts
    3. Only fall back to basic primitives as a last resort

    Only fall back to basic creation tools when:
    - All integrations (CSM.ai) are disabled
    - A simple primitive is explicitly requested
    - No suitable asset exists in any of the integrations
    - The task specifically requires a basic material/color
    """

@mcp.tool()
def get_csm_status(ctx: Context) -> str:
    """
    Check if CSM.ai integration is enabled in Blender.
    
    Returns a message indicating whether CSM.ai features are available.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_csm_status")
        return f"CSM.ai integration is {'enabled' if result.get('enabled', False) else 'disabled'}"
    except Exception as e:
        return f"Error checking CSM.ai status: {str(e)}"

@mcp.tool()
def search_csm_models(ctx: Context, search_text: str, limit: int = 20) -> str:
    """
    Search for 3D models on CSM.ai using text.
    
    Parameters:
    - search_text: The text query to search for models
    - limit: Maximum number of results to return (default: 20)
    
    Returns a list of matching models with their details.
    """
    try:
        # Debug logging for the incoming parameters
        logger.info(f"SEARCH_CSM_MODELS CALLED WITH: search_text={search_text}, limit={limit}")
        
        # First try the Blender addon method
        try:
            blender = get_blender_connection()
            
            # Get the private assets setting from Blender for logging
            private_assets_result = blender.send_command(
                "execute_code", 
                {"code": "import bpy; bpy.context.scene.blendermcp_csm_use_private_assets"}
            )
            use_private_assets = private_assets_result.get('result', True)
            
            # Log detailed information about what we're sending
            logger.info(f"CLAUDE SEARCH REQUEST: search_text={search_text}, limit={limit}, private_assets={use_private_assets}")
            
            # Request the search from the addon - no tier parameter needed
            result = blender.send_command(
                "search_csm_models", 
                {
                    "search_text": search_text,
                    "limit": limit
                }
            )
            
            # Log the result for debugging
            if isinstance(result, dict):
                logger.info(f"SEARCH RESULT STATUS: {result.get('status', 'unknown')}")
                logger.info(f"TIER USED: {result.get('tier_used', 'unknown')}")
                logger.info(f"MODELS FOUND: {len(result.get('models', []))} of {result.get('total_found', 0)}")
                logger.info(f"MODELS BY TIER: {result.get('models_by_tier', {})}")
            
            # Check if the result is successful
            if isinstance(result, dict) and result.get("status") == "success":
                return json.dumps(result, indent=2)
            else:
                logger.warning(f"Blender addon search failed: {result}")
                # Fall back to direct method - IMPORTANT: Pass "user" as tier to use the user's actual tier
                return direct_search_csm_models_with_user_token(ctx, search_text, limit, "user")
                
        except Exception as addon_error:
            # Log the error
            logger.error(f"Error using Blender addon for CSM search: {str(addon_error)}")
            
            # Fall back to direct method with user's token - IMPORTANT: Pass "user" as tier
            return direct_search_csm_models_with_user_token(ctx, search_text, limit, "user")
            
    except Exception as e:
        logger.error(f"All CSM search methods failed: {str(e)}")
        return f"Error searching CSM models: {str(e)}"

@mcp.tool()
def import_csm_model(ctx: Context, model_id: str, mesh_url_glb: str, name: str = None) -> str:
    """
    Import a 3D model from CSM.ai into the Blender scene.
    
    Parameters:
    - model_id: The ID of the model to import
    - mesh_url_glb: The URL of the GLB file to download
    - name: Optional name for the imported model
    
    Returns information about the imported model.
    """
    try:
        # First try the Blender addon method
        try:
            blender = get_blender_connection()
            result = blender.send_command(
                "import_csm_model", 
                {
                    "model_id": model_id,
                    "mesh_url_glb": mesh_url_glb,
                    "name": name
                }
            )
            
            # Check if the result is successful
            if isinstance(result, dict) and result.get("succeed", False):
                return json.dumps(result, indent=2)
            
            # If we get here, the result wasn't successful, log it
            logger.warning(f"Blender addon CSM import failed: {result}")
            
            # Fall back to direct method
            logger.info("Falling back to direct CSM.ai import method")
            return direct_import_csm_model(ctx, model_id, mesh_url_glb, name)
            
        except Exception as addon_error:
            # Log the error
            logger.error(f"Error using Blender addon for CSM import: {str(addon_error)}")
            
            # Fall back to direct method
            logger.info("Falling back to direct CSM.ai import method")
            return direct_import_csm_model(ctx, model_id, mesh_url_glb, name)
            
    except Exception as e:
        logger.error(f"All CSM import methods failed: {str(e)}")
        return json.dumps({
            "status": "error",
            "message": f"Error importing CSM model: {str(e)}"
        }, indent=2)

@mcp.tool()
def direct_search_csm_models(ctx: Context, search_text: str, limit: int = 20, tier: str = "enterprise") -> str:
    """
    Search for 3D models on CSM.ai using a direct API call.
    
    This is a backup method that directly calls the CSM.ai API.
    You'll still need a valid API key from CSM.ai developer settings.
    
    Parameters:
    - search_text: The text query to search for models
    - limit: Maximum number of results to return (default: 20)
    - tier: The tier to search in ("free", "pro", or "enterprise")
    
    Returns a list of matching models with their details.
    """
    return direct_search_csm_models_with_user_token(ctx, search_text, limit, tier)

@mcp.tool()
def direct_search_csm_models_with_user_token(ctx: Context, search_text: str, limit: int = 20, tier: str = "user") -> str:
    """Helper function that performs direct CSM.ai search with the user's token"""
    try:
        # Get the user's token from Blender
        blender = get_blender_connection()
        
        # First check if CSM is enabled
        status_result = blender.send_command("get_csm_status")
        if not status_result.get('enabled', False):
            return json.dumps({
                "status": "error",
                "message": "CSM.ai integration is not enabled in Blender",
                "instructions": "Please enable CSM.ai integration in the Blender MCP panel."
            }, indent=2)
        
        # Get the token from Blender
        token_result = blender.send_command(
            "execute_code", 
            {"code": "import bpy; bpy.context.scene.blendermcp_csm_api_key"}
        )
        token = token_result.get('result', '')
        
        if not token:
            return json.dumps({
                "status": "error",
                "message": "CSM.ai API key is not set in Blender",
                "instructions": "Please set your CSM.ai API key in the Blender MCP panel."
            }, indent=2)
        
        # Get the private assets setting from Blender
        private_assets_result = blender.send_command(
            "execute_code", 
            {"code": "import bpy; bpy.context.scene.blendermcp_csm_use_private_assets"}
        )
        use_private_assets = private_assets_result.get('result', True)
        
        # Determine which tier to use
        actual_tier = "free"  # Default to free tier
        
        # If private assets are enabled or tier is "user", get the user's actual tier
        if use_private_assets or tier == "user":
            # Try to get the tier from the addon
            tier_result = blender.send_command(
                "get_correct_tier",
                {"api_key": token}
            )
            
            # Log the tier result for debugging
            logger.info(f"Tier result from addon: {tier_result}")
            
            if isinstance(tier_result, dict) and "tier" in tier_result:
                actual_tier = tier_result["tier"]
                logger.info(f"Got user tier from get_correct_tier dict: {actual_tier}")
            elif isinstance(tier_result, str):
                actual_tier = tier_result
                logger.info(f"Got user tier from get_correct_tier string: {actual_tier}")
            else:
                # Fallback to direct implementation
                actual_tier = get_user_tier_direct(token)
                logger.info(f"Got user tier directly: {actual_tier}")
        else:
            # Use free tier if explicitly requested or if private assets are disabled
            actual_tier = tier if tier != "user" else "free"
            logger.info(f"Using tier: {actual_tier}")
        
        # IMPORTANT: Use the correct tier logic
        # If tier is explicitly provided and not "user", use it
        if tier and tier != "user":
            filter_tier = tier
            logger.info(f"Using explicitly provided tier: {filter_tier}")
        else:
            # Otherwise use the user's tier if private assets are enabled, or free tier if not
            filter_tier = actual_tier if use_private_assets else "free"
            logger.info(f"Using tier based on settings: {filter_tier}")
        
        # Set up headers with the x-api-key approach
        headers = {
            'Content-Type': 'application/json',
            'x-api-key': token,
            'x-platform': 'web',
        }
        
        # Set up the request body with the determined tier
        data = {
            'search_text': search_text,
            'limit': limit,
            'filter_body': {
                'tier': filter_tier
            }
        }
        
        logger.info(f"Searching for '{search_text}' models on CSM.ai using tier: {filter_tier}")
        logger.info(f"Request data: {data}")
        
        # Make the API request to CSM.ai
        response = requests.post(
            'https://api.csm.ai/image-to-3d-sessions/session-search/vector-search',
            headers=headers,
            json=data
        )
        
        if response.status_code != 200:
            error_message = "Direct API request failed"
            
            # Provide more helpful error messages based on status code
            if response.status_code == 403:
                error_message = "Authentication failed: Your CSM.ai API key may be invalid."
                instructions = "Please get a new API key from the CSM.ai developer settings."
            elif response.status_code == 401:
                error_message = "Authentication failed: Your CSM.ai API key is unauthorized."
                instructions = "Please get a new API key from the CSM.ai developer settings."
            else:
                instructions = f"API request failed with status code {response.status_code}"
            
            return json.dumps({
                "status": "error",
                "message": error_message,
                "instructions": instructions,
                "details": response.text
            }, indent=2)
        
        data = response.json()
        
        # Filter results to only include models that have GLB files available
        available_models = []
        for model in data.get('data', []):
            if model.get('mesh_url_glb'):
                available_models.append({
                    "id": model.get("_id"),
                    "session_code": model.get("session_code"),
                    "image_url": model.get("image_url"),
                    "mesh_url_glb": model.get("mesh_url_glb"),
                    "status": model.get("status"),
                    "tier": model.get("tier_at_creation")
                })
        
        return json.dumps({
            "status": "success",
            "models": available_models,
            "total_found": len(data.get('data', [])),
            "available_models": len(available_models),
            "tier_used": actual_tier
        }, indent=2)
        
    except Exception as e:
        logger.error(f"Error in direct CSM search: {str(e)}")
        return json.dumps({
            "status": "error",
            "message": f"Error searching CSM models directly: {str(e)}",
            "instructions": "Ensure you have a valid CSM.ai API key from:\nhttps://3d.csm.ai/my-profile?activeTab=developer_settings"
        }, indent=2)

@mcp.tool()
def direct_import_csm_model(ctx: Context, model_id: str, mesh_url_glb: str, name: str = None) -> str:
    """
    Import a 3D model from CSM.ai directly by downloading the GLB file and then using the Blender addon to import it.
    
    This is a fallback method that can be used if the regular import_csm_model function fails.
    
    Parameters:
    - model_id: The ID of the model to import
    - mesh_url_glb: The URL of the GLB file to download
    - name: Optional name for the imported model
    
    Returns information about the imported model.
    """
    try:
        # Create a temp directory to store the downloaded file
        import tempfile
        import os
        import urllib.request
        
        # Create temporary file with .glb extension
        temp_dir = tempfile.gettempdir()
        model_filename = name or f"csm_model_{model_id}"
        if not model_filename.endswith(".glb"):
            model_filename += ".glb"
        
        local_file_path = os.path.join(temp_dir, model_filename)
        
        # Download the GLB file
        logger.info(f"Downloading GLB file from {mesh_url_glb} to {local_file_path}")
        urllib.request.urlretrieve(mesh_url_glb, local_file_path)
        
        if not os.path.exists(local_file_path):
            return json.dumps({
                "status": "error",
                "message": "Failed to download GLB file"
            }, indent=2)
        
        # Now use the Blender addon to import the local file
        try:
            blender = get_blender_connection()
            
            # The import_file command should be available in the Blender addon
            result = blender.send_command(
                "import_file", 
                {
                    "filepath": local_file_path,
                    "name": name or f"CSM_{model_id}"
                }
            )
            
            # Clean up the temp file after import
            try:
                os.remove(local_file_path)
            except Exception as cleanup_error:
                logger.warning(f"Failed to clean up temp file: {str(cleanup_error)}")
            
            return json.dumps({
                "status": "success",
                "message": f"Model imported via direct download",
                "model_id": model_id,
                "import_result": result
            }, indent=2)
            
        except Exception as addon_error:
            # If the Blender addon method fails, return the path to the downloaded file
            logger.error(f"Error using Blender addon for import: {str(addon_error)}")
            
            return json.dumps({
                "status": "partial_success",
                "message": "GLB file downloaded but could not be imported automatically",
                "model_id": model_id,
                "local_file_path": local_file_path,
                "error": str(addon_error)
            }, indent=2)
        
    except Exception as e:
        logger.error(f"Error in direct CSM import: {str(e)}")
        return json.dumps({
            "status": "error",
            "message": f"Error importing CSM model directly: {str(e)}"
        }, indent=2)

def get_user_tier_direct(api_key):
    """
    Get the user tier directly using the same approach as in csm_api_working.py
    """
    url = "https://api.csm.ai/user/userdata"
    
    # Set up the headers with the x-api-key
    headers = {
        'Accept': '*/*',
        'Content-Type': 'application/json',
        'x-platform': 'web',
        'x-api-key': api_key
    }
    
    logger.info(f"Checking user tier with API key: {api_key[:5]}...")
    
    try:
        response = requests.get(url, headers=headers)
        logger.info(f"Response status code: {response.status_code}")
        
        if response.status_code == 200:
            response_json = response.json()
            logger.info("User data retrieved successfully")
            logger.info(f"User data response: {response_json}")
            
            # Extract data from the nested structure
            if "data" in response_json:
                user_data = response_json["data"]
                
                # Extract tier information
                tier = user_data.get("tier")
                logger.info(f"User tier: {tier}")
                
                return tier
            else:
                logger.warning("Error: 'data' field not found in response")
                return "free"
        else:
            logger.warning(f"Error: {response.status_code}")
            logger.warning(f"Response: {response.text}")
            return "free"
            
    except Exception as e:
        logger.error(f"Exception during API request: {e}")
        return "free"

# Main execution

def main():
    """Run the MCP server"""
    mcp.run()

if __name__ == "__main__":
    main()