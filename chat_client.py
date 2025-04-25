#!/usr/bin/env python3

import asyncio
import json
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

def extract_text(content):
    """Extract text from a TextContent object or list of TextContent objects."""
    if isinstance(content, list) and content:
        # Return the text from the first TextContent object
        if hasattr(content[0], 'text'):
            return content[0].text
        return str(content[0])
    # If it's a single TextContent object
    elif hasattr(content, 'text'):
        return content.text
    # Return the content as is
    return content

def parse_json_content(content):
    """Parse JSON from the text content if possible."""
    text = extract_text(content)
    if isinstance(text, str):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Not JSON, return as is
            return text
    return text

async def print_available_tools(session):
    """Fetch and print all available tools in a structured format."""
    tools_result = await session.list_tools()
    
    print("\n=== AVAILABLE BLENDER MCP TOOLS ===")
    print(f"Total tools: {len(tools_result.tools)}\n")
    
    # Group tools by category based on their name patterns
    categories = {}
    for tool in tools_result.tools:
        # Determine category by naming convention or function
        if tool.name.startswith("get_"):
            category = "Query Tools"
        elif tool.name.startswith("create_"):
            category = "Creation Tools"
        elif tool.name.startswith("modify_") or tool.name.startswith("set_"):
            category = "Modification Tools"
        elif tool.name.startswith("delete_"):
            category = "Deletion Tools"
        elif "csm" in tool.name.lower():
            category = "CSM.ai Integration"
        elif "execute_" in tool.name:
            category = "Execution Tools"
        elif "animate" in tool.name:
            category = "Animation Tools"
        else:
            category = "Other Tools"
            
        # Add tool to its category
        if category not in categories:
            categories[category] = []
        categories[category].append(tool)
    
    # Print tools by category
    for category, tools in sorted(categories.items()):
        print(f"[{category}]")
        for tool in tools:
            # Get short description (first line)
            desc = tool.description.split('\n')[0] if '\n' in tool.description else tool.description
            print(f"  - {tool.name}: {desc}")
        print()
    
    print("=== END OF TOOLS LIST ===\n")
    return tools_result.tools

async def run():
    # Create server parameters for stdio connection to Blender
    server_params = StdioServerParameters(
        command="python",
        args=["-m", "blender_mcp.server"],
        env=None,  # Use default environment variables
    )

    # Connect to the Blender MCP server via stdio
    async with stdio_client(server_params) as (read_stream, write_stream):
        # Create a ClientSession with the streams
        async with ClientSession(read_stream, write_stream) as session:
            # Initialize the connection
            await session.initialize()
            print("Connected to Blender MCP server")

            # Print all available tools in organized format
            await print_available_tools(session)

            # List available prompts
            prompts_result = await session.list_prompts()
            print(f"Available prompts: {len(prompts_result.prompts)}")
            for prompt in prompts_result.prompts:
                print(f"  - {prompt.name}")

            # Get scene info from Blender
            print("\nGetting Blender scene info...")
            scene_result = await session.call_tool("get_scene_info", arguments={})
            # Parse the scene data
            scene_data = parse_json_content(scene_result.content)
            
            if isinstance(scene_data, dict):
                print(f"Scene name: {scene_data.get('name', 'Unknown')}")
                print(f"Objects in scene: {len(scene_data.get('objects', []))}")
                # Print objects in the scene
                for obj in scene_data.get('objects', []):
                    print(f"  - {obj.get('name')}: {obj.get('type')} at {obj.get('location')}")
            else:
                print(f"Scene data (raw): {scene_data}")
            
            # Create a simple cube
            print("\nCreating a cube...")
            cube_result = await session.call_tool(
                "create_object", 
                arguments={
                    "type": "CUBE",
                    "name": "MCP_Cube",
                    "location": [0, 0, 0],
                    "scale": [1, 1, 1]
                }
            )
            print(f"Result: {extract_text(cube_result.content)}")

            # Get the cube info
            print("\nGetting cube info...")
            cube_info_result = await session.call_tool(
                "get_object_info",
                arguments={"object_name": "MCP_Cube"}
            )
            cube_data = parse_json_content(cube_info_result.content)
            
            if isinstance(cube_data, dict):
                print(f"Cube name: {cube_data.get('name')}")
                print(f"Cube type: {cube_data.get('type')}")
                print(f"Cube location: {cube_data.get('location')}")
                print(f"Cube dimensions: {cube_data.get('world_bounding_box')}")
                print(f"Cube mesh details: vertices={cube_data.get('mesh', {}).get('vertices')}, " +
                      f"polygons={cube_data.get('mesh', {}).get('polygons')}")
            else:
                print(f"Cube info: {cube_data}")

            # Check CSM.ai integration status
            print("\nChecking CSM.ai integration...")
            csm_status_result = await session.call_tool("get_csm_status", arguments={})
            csm_status = extract_text(csm_status_result.content)
            print(f"CSM.ai status: {csm_status}")

if __name__ == "__main__":
    asyncio.run(run())
