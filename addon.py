import bpy
import mathutils
import json
import threading
import socket
import time
import requests
import tempfile
import traceback
import os
import shutil
from bpy.props import StringProperty, IntProperty, BoolProperty, EnumProperty

# Required dependencies
from bpy.types import Operator

bl_info = {
    "name": "Blender MCP",
    "author": "BlenderMCP",
    "version": (0, 2),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > BlenderMCP",
    "description": "Connect Blender to Claude via MCP",
    "category": "Interface",
}

class BlenderMCPServer:
    def __init__(self, host='localhost', port=9876):
        self.host = host
        self.port = port
        self.running = False
        self.socket = None
        self.server_thread = None
    
    def start(self):
        if self.running:
            print("Server is already running")
            return
            
        self.running = True
        
        try:
            # Create socket
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(1)
            
            # Start server thread
            self.server_thread = threading.Thread(target=self._server_loop)
            self.server_thread.daemon = True
            self.server_thread.start()
            
            print(f"BlenderMCP server started on {self.host}:{self.port}")
        except Exception as e:
            print(f"Failed to start server: {str(e)}")
            self.stop()
            
    def stop(self):
        self.running = False
        
        # Close socket
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
            self.socket = None
        
        # Wait for thread to finish
        if self.server_thread:
            try:
                if self.server_thread.is_alive():
                    self.server_thread.join(timeout=1.0)
            except:
                pass
            self.server_thread = None
        
        print("BlenderMCP server stopped")
    
    def _server_loop(self):
        """Main server loop in a separate thread"""
        print("Server thread started")
        self.socket.settimeout(1.0)  # Timeout to allow for stopping
        
        while self.running:
            try:
                # Accept new connection
                try:
                    client, address = self.socket.accept()
                    print(f"Connected to client: {address}")
                    
                    # Handle client in a separate thread
                    client_thread = threading.Thread(
                        target=self._handle_client,
                        args=(client,)
                    )
                    client_thread.daemon = True
                    client_thread.start()
                except socket.timeout:
                    # Just check running condition
                    continue
                except Exception as e:
                    print(f"Error accepting connection: {str(e)}")
                    time.sleep(0.5)
            except Exception as e:
                print(f"Error in server loop: {str(e)}")
                if not self.running:
                    break
                time.sleep(0.5)
        
        print("Server thread stopped")
    
    def _handle_client(self, client):
        """Handle connected client"""
        print("Client handler started")
        client.settimeout(None)  # No timeout
        buffer = b''
        
        try:
            while self.running:
                # Receive data
                try:
                    data = client.recv(8192)
                    if not data:
                        print("Client disconnected")
                        break
                    
                    buffer += data
                    try:
                        # Try to parse command
                        command = json.loads(buffer.decode('utf-8'))
                        buffer = b''
                        
                        # Execute command in Blender's main thread
                        def execute_wrapper():
                            try:
                                response = self.execute_command(command)
                                response_json = json.dumps(response)
                                try:
                                    client.sendall(response_json.encode('utf-8'))
                                except:
                                    print("Failed to send response - client disconnected")
                            except Exception as e:
                                print(f"Error executing command: {str(e)}")
                                traceback.print_exc()
                                try:
                                    error_response = {
                                        "status": "error",
                                        "message": str(e)
                                    }
                                    client.sendall(json.dumps(error_response).encode('utf-8'))
                                except:
                                    pass
                            return None
                        
                        # Schedule execution in main thread
                        bpy.app.timers.register(execute_wrapper, first_interval=0.0)
                    except json.JSONDecodeError:
                        # Incomplete data, wait for more
                        pass
                except Exception as e:
                    print(f"Error receiving data: {str(e)}")
                    break
        except Exception as e:
            print(f"Error in client handler: {str(e)}")
        finally:
            try:
                client.close()
            except:
                pass
            print("Client handler stopped")

    def execute_command(self, command):
        """Execute a command in the main Blender thread"""
        try:
            cmd_type = command.get("type")
            params = command.get("params", {})
            
            # Ensure we're in the right context
            if cmd_type in ["create_object", "modify_object", "delete_object"]:
                override = bpy.context.copy()
                override['area'] = [area for area in bpy.context.screen.areas if area.type == 'VIEW_3D'][0]
                with bpy.context.temp_override(**override):
                    return self._execute_command_internal(command)
            else:
                return self._execute_command_internal(command)
                
        except Exception as e:
            print(f"Error executing command: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def _execute_command_internal(self, command):
        """Internal command execution with proper context"""
        cmd_type = command.get("type")
        params = command.get("params", {})
        
        # Base handlers that are always available
        handlers = {
            "get_scene_info": self.get_scene_info,
            "create_object": self.create_object,
            "modify_object": self.modify_object,
            "delete_object": self.delete_object,
            "get_object_info": self.get_object_info,
            "execute_code": self.execute_code,
            "set_material": self.set_material,
            "get_csm_status": self.get_csm_status,
            "search_csm_models": self.search_csm_models,
            "import_csm_model": lambda **kwargs: self.import_csm_model(**kwargs),
            "animate_object": lambda **kwargs: self.animate_object(**kwargs),
            "get_correct_tier": lambda **kwargs: self.get_correct_tier(**kwargs),
            "import_file": lambda **kwargs: self.import_file(**kwargs),
        }

        handler = handlers.get(cmd_type)
        if handler:
            try:
                print(f"Executing handler for {cmd_type}")
                result = handler(**params)
                print(f"Handler execution complete")
                return {"status": "success", "result": result}
            except Exception as e:
                print(f"Error in handler: {str(e)}")
                traceback.print_exc()
                return {"status": "error", "message": str(e)}
        else:
            return {"status": "error", "message": f"Unknown command type: {cmd_type}"}

    
    def get_simple_info(self):
        """Get basic Blender information"""
        return {
            "blender_version": ".".join(str(v) for v in bpy.app.version),
            "scene_name": bpy.context.scene.name,
            "object_count": len(bpy.context.scene.objects)
        }
    
    def get_scene_info(self):
        """Get information about the current Blender scene"""
        try:
            print("Getting scene info...")
            # Simplify the scene info to reduce data size
            scene_info = {
                "name": bpy.context.scene.name,
                "object_count": len(bpy.context.scene.objects),
                "objects": [],
                "materials_count": len(bpy.data.materials),
            }
            
            # Collect minimal object information (limit to first 10 objects)
            for i, obj in enumerate(bpy.context.scene.objects):
                if i >= 10:  # Reduced from 20 to 10
                    break
                    
                obj_info = {
                    "name": obj.name,
                    "type": obj.type,
                    # Only include basic location data
                    "location": [round(float(obj.location.x), 2), 
                                round(float(obj.location.y), 2), 
                                round(float(obj.location.z), 2)],
                }
                scene_info["objects"].append(obj_info)
            
            print(f"Scene info collected: {len(scene_info['objects'])} objects")
            return scene_info
        except Exception as e:
            print(f"Error in get_scene_info: {str(e)}")
            traceback.print_exc()
            return {"error": str(e)}
    
    @staticmethod
    def _get_aabb(obj):
        """ Returns the world-space axis-aligned bounding box (AABB) of an object. """
        if obj.type != 'MESH':
            raise TypeError("Object must be a mesh")

        # Get the bounding box corners in local space
        local_bbox_corners = [mathutils.Vector(corner) for corner in obj.bound_box]

        # Convert to world coordinates
        world_bbox_corners = [obj.matrix_world @ corner for corner in local_bbox_corners]

        # Compute axis-aligned min/max coordinates
        min_corner = mathutils.Vector(map(min, zip(*world_bbox_corners)))
        max_corner = mathutils.Vector(map(max, zip(*world_bbox_corners)))

        return [
            [*min_corner], [*max_corner]
        ]

    def create_object(self, type="CUBE", name=None, location=(0, 0, 0), rotation=(0, 0, 0), scale=(1, 1, 1),
                    align="WORLD", major_segments=48, minor_segments=12, mode="MAJOR_MINOR",
                    major_radius=1.0, minor_radius=0.25, abso_major_rad=1.25, abso_minor_rad=0.75, generate_uvs=True):
        """Create a new object in the scene"""
        try:
            # Deselect all objects first
            bpy.ops.object.select_all(action='DESELECT')
            
            # Create the object based on type
            if type == "CUBE":
                bpy.ops.mesh.primitive_cube_add(location=location, rotation=rotation, scale=scale)
            elif type == "SPHERE":
                bpy.ops.mesh.primitive_uv_sphere_add(location=location, rotation=rotation, scale=scale)
            elif type == "CYLINDER":
                bpy.ops.mesh.primitive_cylinder_add(location=location, rotation=rotation, scale=scale)
            elif type == "PLANE":
                bpy.ops.mesh.primitive_plane_add(location=location, rotation=rotation, scale=scale)
            elif type == "CONE":
                bpy.ops.mesh.primitive_cone_add(location=location, rotation=rotation, scale=scale)
            elif type == "TORUS":
                bpy.ops.mesh.primitive_torus_add(
                    align=align,
                    location=location,
                    rotation=rotation,
                    major_segments=major_segments,
                    minor_segments=minor_segments,
                    mode=mode,
                    major_radius=major_radius,
                    minor_radius=minor_radius,
                    abso_major_rad=abso_major_rad,
                    abso_minor_rad=abso_minor_rad,
                    generate_uvs=generate_uvs
                )
            elif type == "EMPTY":
                bpy.ops.object.empty_add(location=location, rotation=rotation, scale=scale)
            elif type == "CAMERA":
                bpy.ops.object.camera_add(location=location, rotation=rotation)
            elif type == "LIGHT":
                bpy.ops.object.light_add(type='POINT', location=location, rotation=rotation, scale=scale)
            else:
                raise ValueError(f"Unsupported object type: {type}")
            
            # Force update the view layer
            bpy.context.view_layer.update()
            
            # Get the active object (which should be our newly created object)
            obj = bpy.context.view_layer.objects.active
            
            # If we don't have an active object, something went wrong
            if obj is None:
                raise RuntimeError("Failed to create object - no active object")
            
            # Make sure it's selected
            obj.select_set(True)
            
            # Rename if name is provided
            if name:
                obj.name = name
                if obj.data:
                    obj.data.name = name
            
            # Patch for PLANE: scale don't work with bpy.ops.mesh.primitive_plane_add()
            if type in {"PLANE"}:
                obj.scale = scale

            # Return the object info
            result = {
                "name": obj.name,
                "type": obj.type,
                "location": [obj.location.x, obj.location.y, obj.location.z],
                "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
                "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            }
            
            if obj.type == "MESH":
                bounding_box = self._get_aabb(obj)
                result["world_bounding_box"] = bounding_box
            
            return result
        except Exception as e:
            print(f"Error in create_object: {str(e)}")
            traceback.print_exc()
            return {"error": str(e)}

    def modify_object(self, name, location=None, rotation=None, scale=None, visible=None):
        """Modify an existing object in the scene"""
        # Find the object by name
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")
        
        # Modify properties as requested
        if location is not None:
            obj.location = location
        
        if rotation is not None:
            obj.rotation_euler = rotation
        
        if scale is not None:
            obj.scale = scale
        
        if visible is not None:
            obj.hide_viewport = not visible
            obj.hide_render = not visible
        
        result = {
            "name": obj.name,
            "type": obj.type,
            "location": [obj.location.x, obj.location.y, obj.location.z],
            "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
            "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            "visible": obj.visible_get(),
        }

        if obj.type == "MESH":
            bounding_box = self._get_aabb(obj)
            result["world_bounding_box"] = bounding_box

        return result

    def delete_object(self, name):
        """Delete an object from the scene"""
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")
        
        # Store the name to return
        obj_name = obj.name
        
        # Select and delete the object
        if obj:
            bpy.data.objects.remove(obj, do_unlink=True)
        
        return {"deleted": obj_name}
    
    def get_object_info(self, name):
        """Get detailed information about a specific object"""
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")
        
        # Basic object info
        obj_info = {
            "name": obj.name,
            "type": obj.type,
            "location": [obj.location.x, obj.location.y, obj.location.z],
            "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
            "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            "visible": obj.visible_get(),
            "materials": [],
        }

        if obj.type == "MESH":
            bounding_box = self._get_aabb(obj)
            obj_info["world_bounding_box"] = bounding_box
        
        # Add material slots
        for slot in obj.material_slots:
            if slot.material:
                obj_info["materials"].append(slot.material.name)
        
        # Add mesh data if applicable
        if obj.type == 'MESH' and obj.data:
            mesh = obj.data
            obj_info["mesh"] = {
                "vertices": len(mesh.vertices),
                "edges": len(mesh.edges),
                "polygons": len(mesh.polygons),
            }
        
        return obj_info
    
    def execute_code(self, code):
        """Execute arbitrary Blender Python code"""
        # This is powerful but potentially dangerous - use with caution
        try:
            # Create a local namespace for execution
            namespace = {"bpy": bpy}
            
            # Use eval if the code is a simple expression
            if ";" not in code and "\n" not in code and "=" not in code:
                try:
                    # Try to evaluate as expression
                    result = eval(code, namespace)
                    return {"executed": True, "result": result}
                except SyntaxError:
                    # Not a simple expression, execute as statement
                    exec(code, namespace)
                    return {"executed": True}
            else:
                # For multi-line code or statements (not expressions)
                exec(code, namespace)
                return {"executed": True}
        except Exception as e:
            raise Exception(f"Code execution error: {str(e)}")
    
    def set_material(self, object_name, material_name=None, create_if_missing=True, color=None):
        """Set or create a material for an object"""
        try:
            # Get the object
            obj = bpy.data.objects.get(object_name)
            if not obj:
                raise ValueError(f"Object not found: {object_name}")
            
            # Make sure object can accept materials
            if not hasattr(obj, 'data') or not hasattr(obj.data, 'materials'):
                raise ValueError(f"Object {object_name} cannot accept materials")
            
            # Create or get material
            if material_name:
                mat = bpy.data.materials.get(material_name)
                if not mat and create_if_missing:
                    mat = bpy.data.materials.new(name=material_name)
                    print(f"Created new material: {material_name}")
            else:
                # Generate unique material name if none provided
                mat_name = f"{object_name}_material"
                mat = bpy.data.materials.get(mat_name)
                if not mat:
                    mat = bpy.data.materials.new(name=mat_name)
                material_name = mat_name
                print(f"Using material: {mat_name}")
            
            # Set up material nodes if needed
            if mat:
                if not mat.use_nodes:
                    mat.use_nodes = True
                
                # Get or create Principled BSDF
                principled = mat.node_tree.nodes.get('Principled BSDF')
                if not principled:
                    principled = mat.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
                    # Get or create Material Output
                    output = mat.node_tree.nodes.get('Material Output')
                    if not output:
                        output = mat.node_tree.nodes.new('ShaderNodeOutputMaterial')
                    # Link if not already linked
                    if not principled.outputs[0].links:
                        mat.node_tree.links.new(principled.outputs[0], output.inputs[0])
                
                # Set color if provided
                if color and len(color) >= 3:
                    principled.inputs['Base Color'].default_value = (
                        color[0],
                        color[1],
                        color[2],
                        1.0 if len(color) < 4 else color[3]
                    )
                    print(f"Set material color to {color}")
            
            # Assign material to object if not already assigned
            if mat:
                if not obj.data.materials:
                    obj.data.materials.append(mat)
                else:
                    # Only modify first material slot
                    obj.data.materials[0] = mat
                
                print(f"Assigned material {mat.name} to object {object_name}")
                
                return {
                    "status": "success",
                    "object": object_name,
                    "material": mat.name,
                    "color": color if color else None
                }
            else:
                raise ValueError(f"Failed to create or find material: {material_name}")
            
        except Exception as e:
            print(f"Error in set_material: {str(e)}")
            traceback.print_exc()
            return {
                "status": "error",
                "message": str(e),
                "object": object_name,
                "material": material_name if 'material_name' in locals() else None
            }
    
    def render_scene(self, output_path=None, resolution_x=None, resolution_y=None):
        """Render the current scene"""
        if resolution_x is not None:
            bpy.context.scene.render.resolution_x = resolution_x
        
        if resolution_y is not None:
            bpy.context.scene.render.resolution_y = resolution_y
        
        if output_path:
            bpy.context.scene.render.filepath = output_path
        
        # Render the scene
        bpy.ops.render.render(write_still=bool(output_path))
        
        return {
            "rendered": True,
            "output_path": output_path if output_path else "[not saved]",
            "resolution": [bpy.context.scene.render.resolution_x, bpy.context.scene.render.resolution_y],
        }

    def ensure_valid_csm_token(self):
        """Check if CSM.ai integration is enabled and an API key is set"""
        scene = bpy.context.scene
        if not scene.blendermcp_use_csm:
            # First enable CSM.ai integration
            scene.blendermcp_use_csm = True
            print("CSM.ai integration enabled")
        
        # Check if the API key is set
        if not scene.blendermcp_csm_api_key:
            print("CSM.ai API key is not set. Please get an API key from CSM.ai developer settings.")
            return False
        
        return True

    def get_csm_status(self):
        """Check if CSM.ai integration is enabled and configured"""
        if not bpy.context.scene.blendermcp_use_csm:
            return {"enabled": False, "message": "CSM.ai integration is disabled"}
        
        # Ensure a valid API key is set
        self.ensure_valid_csm_token()
        
        if not bpy.context.scene.blendermcp_csm_api_key:
            return {"enabled": False, "message": "CSM.ai API key is not set"}
        
        return {"enabled": True}

    def search_csm_models(self, search_text, limit=20, tier=None):
        """Search for 3D models on CSM.ai using text"""
        try:
            # Add detailed debug information about all parameters
            print(f"DEBUG: search_csm_models called with search_text={search_text}, limit={limit}, tier={tier}")
            
            if not bpy.context.scene.blendermcp_use_csm:
                return {"status": "error", "message": "CSM.ai integration is disabled"}
            
            # Ensure we have an API key
            if not self.ensure_valid_csm_token():
                return {"status": "error", "message": "CSM.ai API key is not set. Visit https://3d.csm.ai/my-profile?activeTab=developer_settings to get your API key."}
            
            api_key = bpy.context.scene.blendermcp_csm_api_key
            if not api_key:
                return {"status": "error", "message": "CSM.ai API key is not set"}
            
            # Get the private assets setting
            use_private_assets = bpy.context.scene.blendermcp_csm_use_private_assets
            print(f"CSM search - Private assets toggle: {use_private_assets}")
            
            # Determine which tier to use - always check the user's actual tier
            actual_tier = self.get_correct_tier(api_key)
            print(f"Got user tier from API: {actual_tier}")
            
            # If we didn't get a valid tier, use free tier
            if not actual_tier:
                actual_tier = "free"
                print("No valid tier returned, using free tier")
            
            # OVERRIDE THE TIER FOR DEBUGGING
            if tier == "free" and use_private_assets:
                print("WARNING: Tier was explicitly set to 'free' but private assets are enabled!")
                print("DEBUG: Forcibly changing tier from 'free' to user's actual tier")
                tier = None  # Force it to use the user's actual tier
            
            # FIXED: Determine the filter tier correctly
            if tier and tier != "user":
                # If tier is explicitly provided and not "user", use it
                filter_tier = tier
                print(f"Using explicitly provided tier: {filter_tier}")
            else:
                # Otherwise use the user's tier if private assets are enabled, or free tier if not
                filter_tier = actual_tier if use_private_assets else 'free'
                print(f"Using tier based on settings: {filter_tier}")
            
            # Set up the headers
            headers = {
                'Content-Type': 'application/json',
                'x-api-key': api_key,
                'x-platform': 'web',
            }
            
            # Set up the request body
            data = {
                'search_text': search_text,
                'limit': limit,
                'filter_body': {
                    'tier': filter_tier
                }
            }
            
            print(f"Searching for '{search_text}' models on CSM.ai using tier: {filter_tier}")
            print(f"Request data: {data}")
            
            # Make the API request to CSM.ai
            response = requests.post(
                'https://api.csm.ai/image-to-3d-sessions/session-search/vector-search',
                headers=headers,
                json=data
            )
            
            print(f"Response status code: {response.status_code}")
            
            if response.status_code != 200:
                error_details = response.text
                error_message = "API request failed"
                print(f"CSM API error: {error_details}")
                
                # Check for specific error types
                if response.status_code == 403:
                    error_message = "Authentication failed: Your API key may be invalid"
                elif response.status_code == 401:
                    error_message = "Authentication failed: Unauthorized"
                
                return {
                    "status": "error", 
                    "message": f"{error_message} (Status code: {response.status_code})",
                    "details": error_details
                }
            
            data = response.json()
            print(f"Response data: {data}")
            
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
            
            print(f"Found {len(available_models)} models with GLB files out of {len(data.get('data', []))} total models")
            
            return {
                "status": "success",
                "models": available_models,
                "total_found": len(data.get('data', [])),
                "available_models": len(available_models),
                "tier_used": filter_tier,
                "models_by_tier": self._count_models_by_tier(data.get('data', []))
            }
            
        except Exception as e:
            print(f"Error in CSM search: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": f"Error searching CSM models: {str(e)}"}

    def _count_models_by_tier(self, models):
        """Helper function to count models by tier"""
        tier_counts = {}
        for model in models:
            tier = model.get("tier_at_creation", "unknown")
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
        return tier_counts

    def import_csm_model(self, model_id, mesh_url_glb, name=None):
        """Import a 3D model from CSM.ai by its GLB URL"""
        try:
            if not mesh_url_glb:
                return {"status": "error", "message": "No GLB URL provided"}
            
            if not name:
                name = f"CSM_Model_{model_id}"
            
            # Create a temporary file to download the GLB
            temp_file = tempfile.NamedTemporaryFile(
                delete=False,
                prefix=f"csm_{model_id}_",
                suffix=".glb",
            )
            
            try:
                # Download the content
                response = requests.get(mesh_url_glb, stream=True)
                response.raise_for_status()  # Raise an exception for HTTP errors
                
                # Write the content to the temporary file
                for chunk in response.iter_content(chunk_size=8192):
                    temp_file.write(chunk)
                    
                # Close the file
                temp_file.close()
                
            except Exception as e:
                # Clean up the file if there's an error
                temp_file.close()
                os.unlink(temp_file.name)
                return {"succeed": False, "error": str(e)}
            
            try:
                obj = self._clean_imported_glb(
                    filepath=temp_file.name,
                    mesh_name=name
                )
                result = {
                    "name": obj.name,
                    "type": obj.type,
                    "location": [obj.location.x, obj.location.y, obj.location.z],
                    "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
                    "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
                }
                
                if obj.type == "MESH":
                    bounding_box = self._get_aabb(obj)
                    result["world_bounding_box"] = bounding_box
                
                return {
                    "succeed": True, **result
                }
            except Exception as e:
                return {"succeed": False, "error": str(e)}
                
        except Exception as e:
            return {"succeed": False, "error": str(e)}

    @staticmethod
    def _clean_imported_glb(filepath, mesh_name=None):
        """Clean up an imported GLB file by removing empty parent nodes and renaming the mesh"""
        # Get the set of existing objects before import
        existing_objects = set(bpy.data.objects)

        # Import the GLB file
        bpy.ops.import_scene.gltf(filepath=filepath)
        
        # Ensure the context is updated
        bpy.context.view_layer.update()
        
        # Get all imported objects
        imported_objects = list(set(bpy.data.objects) - existing_objects)
        
        if not imported_objects:
            print("Error: No objects were imported.")
            return
        
        # Identify the mesh object
        mesh_obj = None
        
        if len(imported_objects) == 1 and imported_objects[0].type == 'MESH':
            mesh_obj = imported_objects[0]
            print("Single mesh imported, no cleanup needed.")
        else:
            parent_obj = imported_objects[0]
            if parent_obj.type == 'EMPTY' and len(parent_obj.children) == 1:
                potential_mesh = parent_obj.children[0]
                if potential_mesh.type == 'MESH':
                    print("GLB structure confirmed: Empty node with one mesh child.")
                    
                    # Unparent the mesh from the empty node
                    potential_mesh.parent = None
                    
                    # Remove the empty node
                    bpy.data.objects.remove(parent_obj)
                    print("Removed empty node, keeping only the mesh.")
                    
                    mesh_obj = potential_mesh
                else:
                    print("Error: Child is not a mesh object.")
                    return
            else:
                print("Error: Expected an empty node with one mesh child or a single mesh object.")
                return
        
        # Rename the mesh if needed
        try:
            if mesh_obj and mesh_obj.name is not None and mesh_name:
                mesh_obj.name = mesh_name
                if mesh_obj.data.name is not None:
                    mesh_obj.data.name = mesh_name
                print(f"Mesh renamed to: {mesh_name}")
        except Exception as e:
            print("Having issue with renaming, give up renaming.")

        return mesh_obj

    def import_file(self, filepath, name=None):
        """Import a 3D model file into Blender
        
        Parameters:
        - filepath: Path to the 3D model file
        - name: Optional name for the imported model
        
        Returns information about the imported model.
        """
        try:
            if not os.path.exists(filepath):
                return {"succeed": False, "error": f"File not found: {filepath}"}
                
            file_ext = os.path.splitext(filepath)[1].lower()
            
            # Get existing objects before import
            existing_objects = set(bpy.data.objects)
            
            # Import the file based on its extension
            if file_ext in ['.glb', '.gltf']:
                bpy.ops.import_scene.gltf(filepath=filepath)
            elif file_ext == '.fbx':
                bpy.ops.import_scene.fbx(filepath=filepath)
            elif file_ext == '.obj':
                bpy.ops.import_scene.obj(filepath=filepath)
            elif file_ext == '.blend':
                # For blend files, we need to append or link
                with bpy.data.libraries.load(filepath, link=False) as (data_from, data_to):
                    data_to.objects = data_from.objects
                
                # Link the objects to the scene
                for obj in data_to.objects:
                    if obj is not None:
                        bpy.context.collection.objects.link(obj)
            else:
                return {"succeed": False, "error": f"Unsupported file format: {file_ext}"}
            
            # Ensure the context is updated
            bpy.context.view_layer.update()
            
            # Get newly imported objects
            imported_objects = list(set(bpy.data.objects) - existing_objects)
            
            if not imported_objects:
                return {"succeed": False, "error": "No objects were imported"}
            
            # Select all imported objects
            for obj in bpy.context.view_layer.objects:
                obj.select_set(obj in imported_objects)
            
            # Set the active object to the first imported object
            if imported_objects:
                bpy.context.view_layer.objects.active = imported_objects[0]
            
            # If a name is provided, rename the active object
            if name and bpy.context.active_object:
                bpy.context.active_object.name = name
                if bpy.context.active_object.data:
                    bpy.context.active_object.data.name = name
            
            # Compile results
            result = {
                "imported_objects": [obj.name for obj in imported_objects],
                "active_object": bpy.context.active_object.name if bpy.context.active_object else None,
                "filepath": filepath
            }
            
            return {"succeed": True, **result}
            
        except Exception as e:
            return {"succeed": False, "error": str(e)}

    def test_csm_search(self, search_text="blue car", limit=10):
        """
        Test function to debug CSM.ai search directly from Blender
        """
        print("=== TEST CSM SEARCH START ===")
        
        # Get API key
        api_key = bpy.context.scene.blendermcp_csm_api_key
        if not api_key:
            print("ERROR: CSM.ai API key is not set")
            return {"status": "error", "message": "CSM.ai API key is not set"}
        
        # Get private assets setting
        use_private_assets = bpy.context.scene.blendermcp_csm_use_private_assets
        print(f"Private assets setting: {use_private_assets}")
        
        # Get the tier using our get_correct_tier function
        tier = self.get_correct_tier(api_key)
        print(f"User tier from get_correct_tier: {tier}")
        
        # Set up the headers
        headers = {
            'Content-Type': 'application/json',
            'x-api-key': api_key,
            'x-platform': 'web',
        }
        
        # FIXED: Use the correct tier when private assets are enabled
        filter_tier = tier if use_private_assets else 'free'
        print(f"Using filter tier: {filter_tier} (private_assets={use_private_assets})")
        
        # Set up the request body
        data = {
            'search_text': search_text,
            'limit': limit,
            'filter_body': {
                'tier': filter_tier
            }
        }
        
        print(f"Request data: {data}")
        
        # Make the API request to CSM.ai
        response = requests.post(
            'https://api.csm.ai/image-to-3d-sessions/session-search/vector-search',
            headers=headers,
            json=data
        )
        
        print(f"Response status code: {response.status_code}")
        
        if response.status_code != 200:
            print(f"API request failed: {response.text}")
            return {"status": "error", "message": "API request failed"}
        
        data = response.json()
        
        # Count models by tier
        tier_counts = {}
        for model in data.get('data', []):
            model_tier = model.get("tier_at_creation", "unknown")
            tier_counts[model_tier] = tier_counts.get(model_tier, 0) + 1
        
        # Print the first few models to see what we're getting
        print(f"First few models:")
        for i, model in enumerate(data.get('data', [])[:3]):
            print(f"Model {i+1}: ID={model.get('_id')}, Tier={model.get('tier_at_creation')}")
        
        print(f"Models by tier: {tier_counts}")
        print("=== TEST CSM SEARCH END ===")
        
        return {
            "status": "success",
            "tier_used_in_request": filter_tier,
            "models_by_tier": tier_counts,
            "total_found": len(data.get('data', [])),
            "models": data.get('data', [])
        }

    def get_correct_tier(self, api_key=None, get_key_only=False):
        """
        Checks the tier of a CSM.ai user using their API key.
        
        Args:
            api_key: The CSM.ai API key (if None, gets it from Blender)
            get_key_only: If True, just return the API key directly
            
        Returns:
            str: The user's tier information or "free" on error,
                 or the API key if get_key_only is True
        """
        # If no API key is provided, get it from Blender
        if not api_key:
            api_key = bpy.context.scene.blendermcp_csm_api_key
            
        # If we just need the key, return it now
        if get_key_only:
            return api_key
            
        url = "https://api.csm.ai/user/userdata"
        
        # Set up the headers with the x-api-key
        headers = {
            'Accept': '*/*',
            'Content-Type': 'application/json',
            'x-platform': 'web',
            'x-api-key': api_key
        }
        
        print(f"Checking user tier with API key: {api_key[:5]}...")
        
        try:
            response = requests.get(url, headers=headers)
            print(f"Response status code: {response.status_code}")
            
            if response.status_code == 200:
                response_json = response.json()
                print("User data retrieved successfully")
                
                # Extract data from the nested structure
                if "data" in response_json:
                    user_data = response_json["data"]
                    
                    # Extract tier information
                    tier = user_data.get("tier", "free")
                    print(f"User tier: {tier}")
                    
                    # IMPORTANT: Always use the detected tier if private assets are enabled
                    if bpy.context.scene.blendermcp_csm_use_private_assets and tier != "free":
                        print(f"Private assets enabled, using actual tier: {tier}")
                        return tier
                    else:
                        return tier
                else:
                    print("Error: 'data' field not found in response")
                    return "free"
            else:
                print(f"Error: {response.status_code}")
                print(f"Response: {response.text}")
                return "free"
            
        except Exception as e:
            print(f"Exception during API request: {e}")
            return "free"

    def test_claude_search(self, search_text="blue car", limit=10):
        """
        Test function to simulate Claude's search request
        """
        print("=== TEST CLAUDE SEARCH START ===")
        
        # This simulates what happens when Claude calls the search_csm_models function
        result = self.search_csm_models(search_text, limit)
        
        print(f"Result from search_csm_models: {result}")
        print("=== TEST CLAUDE SEARCH END ===")
        
        return result

    def animate_object(self, object_name, animation_prompt, temp_format="glb", handle_original="hide", collection_name=None):
        """
        Animate an object using the animation API
        
        Parameters:
        - object_name: Name of the object to animate
        - animation_prompt: Text description of the desired animation
        - temp_format: Format to use for temporary export (default: "glb")
        - handle_original: How to handle the original object ("keep", "hide", "delete")
        - collection_name: Name of collection to organize animations (if None, creates "{object_name}_Animations")
        
        Returns information about the animated object.
        """
        try:
            import tempfile
            import os
            import base64
            import requests
            
            # Check if the object exists
            obj = bpy.data.objects.get(object_name)
            if not obj:
                # Try to find a backup of the object in the special backup collection
                backup_obj = None
                backup_collection = bpy.data.collections.get("MCP_Backup_Meshes")
                if backup_collection:
                    backup_obj_name = f"{object_name}_backup"
                    for o in backup_collection.objects:
                        if o.name == backup_obj_name:
                            backup_obj = o
                            break
                
                if backup_obj:
                    print(f"Using backup mesh for {object_name}")
                    # Use the backup for exporting but keep the original name for reference
                    obj = backup_obj
                else:
                    raise ValueError(f"Object not found: {object_name}")
            
            # Verify the object is a mesh
            if obj.type != 'MESH':
                raise ValueError(f"Object {object_name} is not a mesh (type: {obj.type})")
            
            # Get or create collection for animations
            if collection_name is None:
                collection_name = f"{object_name}_Animations"
            
            animation_collection = bpy.data.collections.get(collection_name)
            if not animation_collection:
                animation_collection = bpy.data.collections.new(collection_name)
                bpy.context.scene.collection.children.link(animation_collection)
            
            # Ensure we have a backup collection and a backup of this mesh
            backup_collection = bpy.data.collections.get("MCP_Backup_Meshes")
            if not backup_collection:
                backup_collection = bpy.data.collections.new("MCP_Backup_Meshes")
                bpy.context.scene.collection.children.link(backup_collection)
                # Hide the backup collection
                layer_collection = bpy.context.view_layer.layer_collection.children.get("MCP_Backup_Meshes")
                if layer_collection:
                    layer_collection.exclude = True
            
            # Check if we already have a backup of this mesh
            backup_obj_name = f"{object_name}_backup"
            backup_obj = None
            for o in backup_collection.objects:
                if o.name == backup_obj_name:
                    backup_obj = o
                    break
            
            # Flag to track if we're using the original object or a potentially hidden one
            using_original = (obj.name == object_name and not obj.hide_viewport)
            using_backup = (obj.name.endswith("_backup") or obj.hide_viewport)
            
            # If no backup exists, create one
            if not backup_obj:
                # Create a linked duplicate which preserves all mesh data
                backup_obj = bpy.data.objects.new(backup_obj_name, obj.data)
                backup_obj.matrix_world = obj.matrix_world.copy()
                
                # Copy other properties
                backup_obj.scale = obj.scale.copy()
                backup_obj.rotation_euler = obj.rotation_euler.copy()
                backup_obj.location = obj.location.copy()
                
                # Copy materials
                if obj.material_slots:
                    for i, material_slot in enumerate(obj.material_slots):
                        if i >= len(backup_obj.material_slots):
                            backup_obj.data.materials.append(None)
                        if material_slot.material:
                            backup_obj.material_slots[i].material = material_slot.material
                
                # Add to backup collection
                backup_collection.objects.link(backup_obj)
                
                # Hide the backup
                backup_obj.hide_viewport = True
                backup_obj.hide_render = True
                
                print(f"Created linked data backup: {backup_obj_name}")
            else:
                print(f"Using existing backup: {backup_obj_name}")
            
            # Determine which object to use for export
            if using_backup:
                # Create a temporary visible copy for export
                tmp_obj = bpy.data.objects.new(f"{object_name}_temp", backup_obj.data)
                bpy.context.scene.collection.objects.link(tmp_obj)
                
                # Copy transformation
                tmp_obj.matrix_world = backup_obj.matrix_world.copy()
                
                # Make it visible
                tmp_obj.hide_viewport = False
                tmp_obj.hide_render = False
                
                # Use this temporary object for export
                export_obj = tmp_obj
            else:
                # Use the original object for export
                export_obj = obj
            
            # Create a temporary directory for our files
            with tempfile.TemporaryDirectory() as temp_dir:
                # Create temporary filenames
                temp_mesh_path = os.path.join(temp_dir, f"{object_name}_temp.{temp_format}")
                
                # Select only the export object
                bpy.ops.object.select_all(action='DESELECT')
                export_obj.select_set(True)
                bpy.context.view_layer.objects.active = export_obj
                
                # Export the mesh
                if temp_format == "glb":
                    bpy.ops.export_scene.gltf(
                        filepath=temp_mesh_path,
                        export_format='GLB',
                        use_selection=True,
                        export_animations=False
                    )
                else:
                    # Fallback to FBX
                    bpy.ops.export_scene.fbx(
                        filepath=temp_mesh_path,
                        use_selection=True,
                        embed_textures=True
                    )
                
                # Clean up temporary object if we created one
                if 'tmp_obj' in locals() and tmp_obj:
                    bpy.data.objects.remove(tmp_obj)
                
                # Check if file exists
                if not os.path.exists(temp_mesh_path):
                    raise FileNotFoundError(f"Failed to export temporary mesh file: {temp_mesh_path}")
                
                # Clean prompt for output filename
                safe_prompt = animation_prompt.replace(" ", "_").replace("/", "-").lower()
                output_fbx_path = os.path.join(temp_dir, f"{object_name}_{safe_prompt}.fbx")
                
                # Read and encode the mesh file
                with open(temp_mesh_path, "rb") as f:
                    mesh_b64 = base64.b64encode(f.read()).decode("utf-8")
                
                # Build the request payload
                payload = {
                    "mesh_b64_json": mesh_b64,
                    "text_prompt": animation_prompt,
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
                }
                
                # Get CSM API key
                api_key = bpy.context.scene.blendermcp_csm_api_key
                if not api_key:
                    return {
                        "succeed": False,
                        "error": "CSM.ai API key is not set. Please set your API key in the Blender MCP panel."
                    }
                
                # Set up headers with API key
                headers = {
                    'Content-Type': 'application/json',
                    'x-api-key': api_key,
                    'x-platform': 'web'
                }
                
                # Animation server URL
                server_url = "https://animation.csm.ai/animate"
                
                # Send request and stream response to file
                print(f"Sending animation request for prompt: '{animation_prompt}'...")
                resp = requests.post(server_url, json=payload, headers=headers, stream=True)
                
                if resp.status_code != 200:
                    error_text = resp.text
                    print(f"Server returned error {resp.status_code}: {error_text}")
                    return {
                        "succeed": False,
                        "error": f"Animation server error: {resp.status_code}",
                        "details": error_text[:500]  # Limit response size
                    }
                
                # Save the animated FBX file
                with open(output_fbx_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                
                # Check if file was created
                if not os.path.exists(output_fbx_path):
                    return {
                        "succeed": False,
                        "error": "Failed to save animated FBX file"
                    }
                
                # Store original location and parent for reference
                original_location = obj.location.copy()
                original_parent = obj.parent
                original_collection = None
                
                # Find the original object's collection(s)
                for coll in bpy.data.collections:
                    if obj.name in coll.objects:
                        original_collection = coll
                        break
                
                # Import the animated file
                # First store current objects to determine which are new
                existing_objects = set(bpy.data.objects)
                
                # Import the animated FBX
                bpy.ops.import_scene.fbx(filepath=output_fbx_path)
                
                # Get new objects
                imported_objects = list(set(bpy.data.objects) - existing_objects)
                
                if not imported_objects:
                    return {
                        "succeed": False,
                        "error": "Animation imported but no new objects were created"
                    }
                
                # Move new objects to the animation collection
                for new_obj in imported_objects:
                    # First remove from current collections
                    for coll in list(new_obj.users_collection):
                        coll.objects.unlink(new_obj)
                    
                    # Add to our animation collection
                    animation_collection.objects.link(new_obj)
                    
                    # Set a meaningful name based on the animation prompt
                    if new_obj.type == 'ARMATURE':
                        new_obj.name = f"{object_name}_{safe_prompt}_armature"
                    elif new_obj.type == 'MESH':
                        new_obj.name = f"{object_name}_{safe_prompt}"
                
                # Handle the original object according to preference
                # Only apply if we're dealing with the visible original (not a backup or already hidden object)
                if using_original:
                    if handle_original == "hide":
                        # Hide the original but keep it
                        obj.hide_viewport = True
                        obj.hide_render = True
                    elif handle_original == "delete":
                        # Delete the original object, but only if we have a backup
                        if backup_obj:
                            bpy.data.objects.remove(obj)
                # "keep" option just leaves it as is
                
                # Find the main animated objects (usually an armature and a mesh)
                armature_obj = None
                mesh_obj = None
                
                for new_obj in imported_objects:
                    if new_obj.type == 'ARMATURE':
                        armature_obj = new_obj
                    elif new_obj.type == 'MESH':
                        mesh_obj = new_obj
                
                # Create result with imported object info
                result = {
                    "succeed": True,
                    "message": f"Object '{object_name}' animated with prompt: '{animation_prompt}'",
                    "original_object": object_name,
                    "animation_prompt": animation_prompt,
                    "imported_objects": [obj.name for obj in imported_objects],
                    "collection": collection_name,
                    "handle_original": handle_original
                }
                
                if armature_obj:
                    result["armature_object"] = armature_obj.name
                
                if mesh_obj:
                    result["mesh_object"] = mesh_obj.name
                
                return result
                
        except Exception as e:
            print(f"Error in animate_object: {str(e)}")
            traceback.print_exc()
            return {
                "succeed": False,
                "error": str(e)
            }

# Blender UI Panel
class BLENDERMCP_PT_Panel(bpy.types.Panel):
    bl_label = "Blender MCP"
    bl_idname = "BLENDERMCP_PT_Panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BlenderMCP'
    
    def draw(self, context):
        layout = self.layout
        scene = context.scene
        
        layout.prop(scene, "blendermcp_port")

        # Add CSM.ai section
        layout.prop(scene, "blendermcp_use_csm", text="Use CSM.ai 3D models")
        if scene.blendermcp_use_csm:
            layout.prop(scene, "blendermcp_csm_api_key", text="API Key")
            layout.prop(scene, "blendermcp_csm_use_private_assets", text="Include Private Assets")
            layout.operator("blendermcp.get_csm_api_key", text="Get API Key", icon='URL')
        
        if not scene.blendermcp_server_running:
            layout.operator("blendermcp.start_server", text="Start MCP Server")
        else:
            layout.operator("blendermcp.stop_server", text="Stop MCP Server")
            layout.label(text=f"Running on port {scene.blendermcp_port}")

# Operator to start the server
class BLENDERMCP_OT_StartServer(bpy.types.Operator):
    bl_idname = "blendermcp.start_server"
    bl_label = "Connect to Claude"
    bl_description = "Start the BlenderMCP server to connect with Claude"
    
    def execute(self, context):
        scene = context.scene
        
        # Create a new server instance
        if not hasattr(bpy.types, "blendermcp_server") or not bpy.types.blendermcp_server:
            bpy.types.blendermcp_server = BlenderMCPServer(port=scene.blendermcp_port)
        
        # Start the server
        bpy.types.blendermcp_server.start()
        scene.blendermcp_server_running = True
        
        return {'FINISHED'}

# Operator to stop the server
class BLENDERMCP_OT_StopServer(bpy.types.Operator):
    bl_idname = "blendermcp.stop_server"
    bl_label = "Stop the connection to Claude"
    bl_description = "Stop the connection to Claude"
    
    def execute(self, context):
        scene = context.scene
        
        # Stop the server if it exists
        if hasattr(bpy.types, "blendermcp_server") and bpy.types.blendermcp_server:
            bpy.types.blendermcp_server.stop()
            del bpy.types.blendermcp_server
        
        scene.blendermcp_server_running = False
        
        return {'FINISHED'}

# Operator to get CSM.ai API Key
class BLENDERMCP_OT_GetCSMAPIKey(bpy.types.Operator):
    bl_idname = "blendermcp.get_csm_api_key"
    bl_label = "Get API Key"
    bl_description = "Instructions for obtaining a valid CSM.ai API key"
    
    def execute(self, context):
        self.report({'INFO'}, "Visit CSM.ai developer settings to get your API key")
        # Open a popup with instructions
        def draw(self, context):
            layout = self.layout
            layout.label(text="To get your CSM.ai API key:")
            layout.label(text="1. Log in to https://3d.csm.ai/")
            layout.label(text="2. Go to your profile")
            layout.label(text="3. Open the Developer Settings tab")
            layout.label(text="4. URL: https://3d.csm.ai/my-profile?activeTab=developer_settings")
            layout.label(text="5. Copy your API key")
            layout.label(text="6. Paste it in the CSM.ai API Key field")
            
        bpy.context.window_manager.popup_menu(draw, title="Get CSM.ai API Key", icon='INFO')
        return {'FINISHED'}

# Registration functions
def register():
    bpy.types.Scene.blendermcp_port = IntProperty(
        name="Port",
        description="Port for the BlenderMCP server",
        default=9876,
        min=1024,
        max=65535
    )
    
    bpy.types.Scene.blendermcp_server_running = bpy.props.BoolProperty(
        name="Server Running",
        default=False
    )
    
    bpy.types.Scene.blendermcp_use_csm = bpy.props.BoolProperty(
        name="Use CSM.ai",
        description="Enable CSM.ai 3D model integration",
        default=False
    )

    bpy.types.Scene.blendermcp_csm_api_key = bpy.props.StringProperty(
        name="CSM API Key",
        subtype="PASSWORD",
        description="API Key for CSM.ai",
        default=""
    )
    
    bpy.types.Scene.blendermcp_csm_use_private_assets = bpy.props.BoolProperty(
        name="Include Private Assets",
        description="Toggle to include your private assets in search results (requires API key)",
        default=True
    )
    
    bpy.utils.register_class(BLENDERMCP_PT_Panel)
    bpy.utils.register_class(BLENDERMCP_OT_StartServer)
    bpy.utils.register_class(BLENDERMCP_OT_StopServer)
    bpy.utils.register_class(BLENDERMCP_OT_GetCSMAPIKey)
    
    print("BlenderMCP addon registered")

def unregister():
    # Stop the server if it's running
    if hasattr(bpy.types, "blendermcp_server") and bpy.types.blendermcp_server:
        bpy.types.blendermcp_server.stop()
        del bpy.types.blendermcp_server
    
    bpy.utils.unregister_class(BLENDERMCP_PT_Panel)
    bpy.utils.unregister_class(BLENDERMCP_OT_StartServer)
    bpy.utils.unregister_class(BLENDERMCP_OT_StopServer)
    bpy.utils.unregister_class(BLENDERMCP_OT_GetCSMAPIKey)
    
    del bpy.types.Scene.blendermcp_port
    del bpy.types.Scene.blendermcp_server_running
    del bpy.types.Scene.blendermcp_use_csm
    del bpy.types.Scene.blendermcp_csm_api_key
    del bpy.types.Scene.blendermcp_csm_use_private_assets

    print("BlenderMCP addon unregistered")

if __name__ == "__main__":
    register()
