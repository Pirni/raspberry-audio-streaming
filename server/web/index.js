async function loadStreams() {
  const container = document.getElementById('list-container');
  try {

    // Fetch the JSON list from the Nginx autoindex
    const response = await fetch('/hls/');
    
    if (response.status !== 200) {
      container.innerHTML = 
      `
        <p>Failed to load directory listing</p>
        <p>Status: ${response.status}, Error: ${await response.text()}</p>
      `;
      return;
    }

    const items = await response.json();

    container.innerHTML = '';

    // Filter items to find directories (nested HLS streams)
    const streams = items.filter(item => item.type === 'directory');

    if (streams.length === 0) {
      container.innerHTML = '<p>No streams are currently live.</p>';
    } else {
      streams.forEach(s => {
        const link = document.createElement('a');
        link.className = 'stream-link';

        // Links to index.html with the folder name as a parameter
        link.href = `player.html?stream=${s.name}`;
        link.innerHTML = `<span class="live-dot">●</span> Stream: ${s.name}`;
        container.appendChild(link);
      });
    }
  } catch (e) {
    container.innerHTML = '<p>Error loading streams. Ensure autoindex is working.</p>';
  }
}

loadStreams();
// Refresh every 15 seconds
setInterval(loadStreams, 15000);