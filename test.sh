MSSQL_SA_PASSWORD='$up3r$3cr3t$tr0ngP@ssw0rd!' agent-sandbox-cli create-issue-vm \
  --repo-source git@ssh.dev.azure.com:v3/exampleindustry/Example%20Industry%20APP/ExampleIndustryAPP \
  --ttl-hours 24 \
  --dotnet \
  --nuget /home/jonathan/Documents/Repos/ExampleIndustryAPP/Src/App.slnx \
  --mssql \
  --frontend-deps \
  --frontend-host-checkout /home/jonathan/Documents/Repos/ExampleIndustryAPP \
  --frontend-install-root /home/jonathan/Documents/Repos/ExampleIndustryAPP/Src/Clients/AdminClient \
  --frontend-install-root /home/jonathan/Documents/Repos/ExampleIndustryAPP/Src/Clients \
  /home/jonathan/agent-sandbox/tasks/12305.yaml
