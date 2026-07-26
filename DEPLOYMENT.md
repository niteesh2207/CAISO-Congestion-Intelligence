# Deploying to GitHub Pages

## 1. Create the repository

Recommended repository name:

`CAISO-Congestion-Intelligence`

Recommended description:

`AI-powered CAISO congestion intelligence map with constraint/contingency visualization, source/sink logic, trader drivers, and outage-sensitivity explanations.`

Choose **Public** for the easiest portfolio/GitHub Pages setup.

Do **not** initialize the GitHub repository with a README, `.gitignore`, or license if you intend to upload this package exactly as provided; those files are already included.

## 2. Upload the repository package

In the new repository:

1. Select **Add file → Upload files**.
2. Drag the *contents* of this folder into GitHub, so `index.html` is at the repository root.
3. Commit directly to `main` with a message such as:
   `Initial CAISO Congestion Intelligence portfolio release`

## 3. Enable GitHub Pages

1. Open **Settings** in the repository.
2. Select **Pages** under **Code and automation**.
3. Under **Build and deployment**, choose **Deploy from a branch**.
4. Select branch `main` and folder `/ (root)`.
5. Save.

Your default URL will normally follow this pattern:

`https://YOUR-GITHUB-USERNAME.github.io/CAISO-Congestion-Intelligence/`

## 4. Add the live link to the repository

After GitHub Pages is live:

1. Open the repository main page.
2. Edit the repository **About** section.
3. Paste the GitHub Pages URL in the **Website** field.
4. Add topics such as:
   `caiso`, `power-markets`, `energy`, `transmission`, `congestion`, `leaflet`, `ai`, `power-systems`, `electricity-markets`.

## 5. Future custom domain

Later, GitHub Pages can use a custom domain such as:

`congestion.yourdomain.com`

Configure the custom domain in **Settings → Pages**, then add the required DNS record with the domain provider. Verify the domain before use when possible.
