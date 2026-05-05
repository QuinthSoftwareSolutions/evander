@secure()
param extensions_enablevmAccess_username string

@secure()
param extensions_enablevmAccess_password string

@secure()
param extensions_enablevmAccess_ssh_key string

@secure()
param extensions_enablevmAccess_reset_ssh string

@secure()
param extensions_enablevmAccess_remove_user string

@secure()
param extensions_enablevmAccess_expiration string

@secure()
param extensions_enablevmAccess_username_1 string

@secure()
param extensions_enablevmAccess_password_1 string

@secure()
param extensions_enablevmAccess_ssh_key_1 string

@secure()
param extensions_enablevmAccess_reset_ssh_1 string

@secure()
param extensions_enablevmAccess_remove_user_1 string

@secure()
param extensions_enablevmAccess_expiration_1 string
param sshPublicKeys_agent_worker_name string = 'agent-worker'
param virtualMachines_truman_worker_1_name string = 'truman-worker-1'
param sshPublicKeys_truman_worker_1_key_name string = 'truman-worker-1_key'
param virtualMachines_truman_control_center_name string = 'truman-control-center'
param sshPublicKeys_truman_control_center_key_name string = 'truman-control-center_key'
param sshPublicKeys_truman_user_control_plane_name string = 'truman-user-control-plane'
param networkInterfaces_truman_worker_1335_z1_name string = 'truman-worker-1335_z1'
param publicIPAddresses_truman_control_center_ip_name string = 'truman-control-center-ip'
param virtualNetworks_truman_control_center_vnet_name string = 'truman-control-center-vnet'
param networkInterfaces_truman_control_center992_z1_name string = 'truman-control-center992_z1'
param networkSecurityGroups_nsg_agent_worker_subnet_name string = 'nsg-agent-worker-subnet'
param networkSecurityGroups_truman_control_center_nsg_name string = 'truman-control-center-nsg'

resource sshPublicKeys_agent_worker_name_resource 'Microsoft.Compute/sshPublicKeys@2025-04-01' = {
  name: sshPublicKeys_agent_worker_name
  location: 'northeurope'
  properties: {
    publicKey: 'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCkxboB05OHcQo2qpPZDinpKsqQjRI84/66lAOSaTYz0ptz3/tGMETnRS235BmVJ/kNfhkABcWPEFs94TcKsz/7RuPQ0PL1HYP0NwmYEQVBuwHRfjLY5zO0Kz/mZon1m5SJPxsEIRmlil0Kc/GhPN2UgwfvWg0QhUpjA39qPaA4SJ1g8wm6ywjuKkhQAzdrTGTWK8MVQxS/Qc2zeX6C+CZb4LmfEpLd+aPghtofT0npOzj5t+576IJ0GIKKUWdoSsde0TUgwHo7tWXjLyvMPozlJRyU21+MQHUI45PlqckLNyOi1HmuZprppil4Xm6+r86Sb6G3QDfsP5CAHg9XqracA8fqZmEH04JPQEyHLzJT3Ci2rCi8Oownj1gBBUWMaitPDIDK8WlDE30X7nY5MJgroNrNOWobwiwKhODLR8jIR9RBtHGqkKfBwA18dEazG1E5iH5SLDqmf5grddY9MZgRzvHmI6tJc7XNepft3AgoU2cZvW3m5uVANHBTk2Thmek= generated-by-azure'
  }
}

resource sshPublicKeys_truman_control_center_key_name_resource 'Microsoft.Compute/sshPublicKeys@2025-04-01' = {
  name: sshPublicKeys_truman_control_center_key_name
  location: 'northeurope'
  properties: {
    publicKey: 'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC8jQcE2hao6UtD+fW0esx815/HpmdCp9MDX0Z29vjmPYme8pZp/EuviAWNbNvYMGpwPPsYHOX2TGh0Dy/nCenn/po1K6sjgoHdruuJLSre+KjKVIdyN0YsVkWvMDS99A3AFpQfyBjNMvZgG6aBFmikHvGQFfSJdNDx5wyyJ1yryZNDrqlmKn36bOIH62wi3hM/2Mdg/Pdvyq9oaoFhXoe8INFrWJOY3jLj5z1TXv14pBXdDSlywLq7XO8gepMAUfwEpjGdZHLyvsbDCdzOYhzygVNqubgSY+f+Qcx2T8IEBvFJcObfR4kT5kWXKB9RtL9+LRKHKoPYgJK0Vl65luETY6p3I/XarNjeMceXtylTIAACXteqVbF3MXe2WrrOEoFdkbL7c/AyJjQSOKxbc2XstsnO+gak36bJX+RdhvBe9tBZeadPidbaDEbQb2xtU9BWJWhbr6AjPnG2JKztR9M/AB3gvIDovuUUe1Gy99YwU6BcROGIVtIvKjeVbd4IINE= generated-by-azure'
  }
}

resource sshPublicKeys_truman_user_control_plane_name_resource 'Microsoft.Compute/sshPublicKeys@2025-04-01' = {
  name: sshPublicKeys_truman_user_control_plane_name
  location: 'northeurope'
  properties: {
    publicKey: 'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQDQe2zJjkDjfLKVWd5/QOlW1W2PIcg/xUpDNoq5Xxgn/ibGZUecAOCQsem4ytMWpXAQ6OPtnK976yJ1TIrfeWsiEMdXbafzzFB1Hq14dVol4N3sTAEhmvBy3Gid9ore1IZQhJs3Vd7g/Ym01JLGOpien9zG0WI0vC33LikxVoCeBGsfYPXf0Do9FG5PZsRk+mnO+UWvcnciwn+S4ni4poQtEANmyCgTXPRDIoHTsNwQXYtN02jc3UaBr3IkusEKC/8kV/ZDaijVA8LkarJ9+c5Xl/QUQswftErTxNqkhkHWtCBbynMBQcAMwiNJcaLdSbVBWsozVH0bYKK/aZdOOK5s6/XulFmbO/4+NP1Smu8zFVIeo57lrlVNAyvSgrE9lnj9RIQpRi2M57BcxEwmYmv9v4eSZhnRwY9BT8TrkmIdCnc5eEFfboNhBesHO8LrPoD2MAB/bluPNfiQ2ruxM71iWPgka64BXXQaL90hpQHt43VxPkO+O5JTNR1DIKS1+lk= generated-by-azure'
  }
}

resource sshPublicKeys_truman_worker_1_key_name_resource 'Microsoft.Compute/sshPublicKeys@2025-04-01' = {
  name: sshPublicKeys_truman_worker_1_key_name
  location: 'northeurope'
  properties: {
    publicKey: 'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQDMEzV2TGLHxjFnW6TtzSZGShPnUcc7ktzwPyUZHK+dK/wja3Q7mA1ClKMLkKvMwuyzhocpua4dGYUUfTMeHAC5SXlIsHn5uEeH/qFoCzBlBmBHFVuRMAraf7X8do04cCB04dJR7rmbFor4UTZ92XJhUxG0rK6MFjcWSWR+Q8TIcBChFz6eVphWePHBEAcGsHJQ/JuMSnERSpF72UqIfjdVrgPsH+ezrbAGvhyRfYb+vDwMUPY3bY/o7o9hZhjQF3pSKbv0BWB77I2IXZKhJiD14KJP87lcmp6LruvxZtVJ8h8u298e1Zu2f1P505FgBv/zoILxBMgGUfnqjCRdf2RSTUKcVQBr1m3cM/ZqAY0vgJ9nQAJs+D5yXOZra1PuCTtg3xpZszUqqpmgMtuBqzf1u8BfShSJE8zKqJPI2rDNBsgAZ7jlP6Ze1CPgzy0/YcAYPaJBnWHfIY6EYG7uBpsx21cp33LqNeG5nK5d9nmRD9WgUWWVmq8Qv1aJ1b0C3IU= generated-by-azure'
  }
}

resource networkSecurityGroups_nsg_agent_worker_subnet_name_resource 'Microsoft.Network/networkSecurityGroups@2025-05-01' = {
  name: networkSecurityGroups_nsg_agent_worker_subnet_name
  location: 'northeurope'
  properties: {
    securityRules: [
      {
        name: 'Allow-ControlPlane-WorkerDaemon-11888'
        id: networkSecurityGroups_nsg_agent_worker_subnet_name_Allow_ControlPlane_WorkerDaemon_11888.id
        type: 'Microsoft.Network/networkSecurityGroups/securityRules'
        properties: {
          protocol: 'TCP'
          sourcePortRange: '*'
          sourceAddressPrefix: '10.2.0.4/32'
          destinationAddressPrefix: '*'
          access: 'Allow'
          priority: 200
          direction: 'Inbound'
          sourcePortRanges: []
          destinationPortRanges: [
            '11888'
            '22'
          ]
          sourceAddressPrefixes: []
          destinationAddressPrefixes: []
        }
      }
      {
        name: 'Deny_Internet'
        id: networkSecurityGroups_nsg_agent_worker_subnet_name_Deny_Internet.id
        type: 'Microsoft.Network/networkSecurityGroups/securityRules'
        properties: {
          protocol: '*'
          sourcePortRange: '*'
          destinationPortRange: '*'
          sourceAddressPrefix: '*'
          destinationAddressPrefix: 'Internet'
          access: 'Deny'
          priority: 210
          direction: 'Outbound'
          sourcePortRanges: []
          destinationPortRanges: []
          sourceAddressPrefixes: []
          destinationAddressPrefixes: []
        }
      }
      {
        name: 'DenyAllInbound'
        id: networkSecurityGroups_nsg_agent_worker_subnet_name_DenyAllInbound.id
        type: 'Microsoft.Network/networkSecurityGroups/securityRules'
        properties: {
          protocol: '*'
          sourcePortRange: '*'
          destinationPortRange: '*'
          sourceAddressPrefix: '*'
          destinationAddressPrefix: '*'
          access: 'Deny'
          priority: 400
          direction: 'Inbound'
          sourcePortRanges: []
          destinationPortRanges: []
          sourceAddressPrefixes: []
          destinationAddressPrefixes: []
        }
      }
      {
        name: 'AllowALB'
        id: networkSecurityGroups_nsg_agent_worker_subnet_name_AllowALB.id
        type: 'Microsoft.Network/networkSecurityGroups/securityRules'
        properties: {
          protocol: '*'
          sourcePortRange: '*'
          destinationPortRange: '*'
          sourceAddressPrefix: 'AzureLoadBalancer'
          destinationAddressPrefix: '*'
          access: 'Allow'
          priority: 300
          direction: 'Inbound'
          sourcePortRanges: []
          destinationPortRanges: []
          sourceAddressPrefixes: []
          destinationAddressPrefixes: []
        }
      }
    ]
  }
}

resource networkSecurityGroups_truman_control_center_nsg_name_resource 'Microsoft.Network/networkSecurityGroups@2025-05-01' = {
  name: networkSecurityGroups_truman_control_center_nsg_name
  location: 'northeurope'
  properties: {
    securityRules: [
      {
        name: 'SSH'
        id: networkSecurityGroups_truman_control_center_nsg_name_SSH.id
        type: 'Microsoft.Network/networkSecurityGroups/securityRules'
        properties: {
          protocol: 'TCP'
          sourcePortRange: '*'
          destinationPortRange: '22'
          sourceAddressPrefix: '90.224.191.157'
          destinationAddressPrefix: '*'
          access: 'Allow'
          priority: 300
          direction: 'Inbound'
          sourcePortRanges: []
          destinationPortRanges: []
          sourceAddressPrefixes: []
          destinationAddressPrefixes: []
        }
      }
      {
        name: 'HTTPS'
        id: networkSecurityGroups_truman_control_center_nsg_name_HTTPS.id
        type: 'Microsoft.Network/networkSecurityGroups/securityRules'
        properties: {
          protocol: 'TCP'
          sourcePortRange: '*'
          destinationPortRange: '443'
          sourceAddressPrefix: '*'
          destinationAddressPrefix: '*'
          access: 'Allow'
          priority: 310
          direction: 'Inbound'
          sourcePortRanges: []
          destinationPortRanges: []
          sourceAddressPrefixes: []
          destinationAddressPrefixes: []
        }
      }
      {
        name: 'HTTP'
        id: networkSecurityGroups_truman_control_center_nsg_name_HTTP.id
        type: 'Microsoft.Network/networkSecurityGroups/securityRules'
        properties: {
          protocol: 'TCP'
          sourcePortRange: '*'
          destinationPortRange: '80'
          sourceAddressPrefix: '*'
          destinationAddressPrefix: '*'
          access: 'Allow'
          priority: 320
          direction: 'Inbound'
          sourcePortRanges: []
          destinationPortRanges: []
          sourceAddressPrefixes: []
          destinationAddressPrefixes: []
        }
      }
      {
        name: 'WorkerPorts'
        id: networkSecurityGroups_truman_control_center_nsg_name_WorkerPorts.id
        type: 'Microsoft.Network/networkSecurityGroups/securityRules'
        properties: {
          protocol: 'TCP'
          sourcePortRange: '*'
          sourceAddressPrefix: '10.2.1.0/27'
          destinationAddressPrefix: '*'
          access: 'Allow'
          priority: 330
          direction: 'Inbound'
          sourcePortRanges: []
          destinationPortRanges: [
            '22'
            '8000'
            '11434'
            '11666'
            '11777'
            '8081'
            '3142'
          ]
          sourceAddressPrefixes: []
          destinationAddressPrefixes: []
        }
      }
      {
        name: 'DenyWorkerInbound'
        id: networkSecurityGroups_truman_control_center_nsg_name_DenyWorkerInbound.id
        type: 'Microsoft.Network/networkSecurityGroups/securityRules'
        properties: {
          protocol: '*'
          sourcePortRange: '*'
          destinationPortRange: '*'
          sourceAddressPrefix: '10.2.1.0/27'
          destinationAddressPrefix: '*'
          access: 'Deny'
          priority: 340
          direction: 'Inbound'
          sourcePortRanges: []
          destinationPortRanges: []
          sourceAddressPrefixes: []
          destinationAddressPrefixes: []
        }
      }
    ]
  }
}

resource publicIPAddresses_truman_control_center_ip_name_resource 'Microsoft.Network/publicIPAddresses@2025-05-01' = {
  name: publicIPAddresses_truman_control_center_ip_name
  location: 'northeurope'
  sku: {
    name: 'Standard'
    tier: 'Regional'
  }
  zones: [
    '1'
  ]
  properties: {
    ipAddress: '52.158.33.185'
    publicIPAddressVersion: 'IPv4'
    publicIPAllocationMethod: 'Static'
    idleTimeoutInMinutes: 4
    dnsSettings: {
      domainNameLabel: 'truman-admin'
      fqdn: 'truman-admin.northeurope.cloudapp.azure.com'
    }
    ipTags: []
    ddosSettings: {
      protectionMode: 'VirtualNetworkInherited'
    }
  }
}

resource virtualMachines_truman_control_center_name_resource 'Microsoft.Compute/virtualMachines@2025-04-01' = {
  name: virtualMachines_truman_control_center_name
  location: 'northeurope'
  zones: [
    '1'
  ]
  properties: {
    hardwareProfile: {
      vmSize: 'Standard_D2as_v5'
    }
    additionalCapabilities: {
      hibernationEnabled: false
    }
    storageProfile: {
      imageReference: {
        publisher: 'canonical'
        offer: 'ubuntu-24_04-lts'
        sku: 'server'
        version: 'latest'
      }
      osDisk: {
        osType: 'Linux'
        name: '${virtualMachines_truman_control_center_name}_OsDisk_1_6adcb3deb83246089bdc59a6dff66927'
        createOption: 'FromImage'
        caching: 'ReadWrite'
        managedDisk: {
          storageAccountType: 'Premium_LRS'
          id: resourceId(
            'Microsoft.Compute/disks',
            '${virtualMachines_truman_control_center_name}_OsDisk_1_6adcb3deb83246089bdc59a6dff66927'
          )
        }
        deleteOption: 'Delete'
        diskSizeGB: 30
      }
      dataDisks: []
      diskControllerType: 'SCSI'
    }
    osProfile: {
      computerName: virtualMachines_truman_control_center_name
      linuxConfiguration: {
        disablePasswordAuthentication: true
        ssh: {
          publicKeys: [
            {
              path: '/home/azureuser/.ssh/authorized_keys'
              keyData: 'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC8jQcE2hao6UtD+fW0esx815/HpmdCp9MDX0Z29vjmPYme8pZp/EuviAWNbNvYMGpwPPsYHOX2TGh0Dy/nCenn/po1K6sjgoHdruuJLSre+KjKVIdyN0YsVkWvMDS99A3AFpQfyBjNMvZgG6aBFmikHvGQFfSJdNDx5wyyJ1yryZNDrqlmKn36bOIH62wi3hM/2Mdg/Pdvyq9oaoFhXoe8INFrWJOY3jLj5z1TXv14pBXdDSlywLq7XO8gepMAUfwEpjGdZHLyvsbDCdzOYhzygVNqubgSY+f+Qcx2T8IEBvFJcObfR4kT5kWXKB9RtL9+LRKHKoPYgJK0Vl65luETY6p3I/XarNjeMceXtylTIAACXteqVbF3MXe2WrrOEoFdkbL7c/AyJjQSOKxbc2XstsnO+gak36bJX+RdhvBe9tBZeadPidbaDEbQb2xtU9BWJWhbr6AjPnG2JKztR9M/AB3gvIDovuUUe1Gy99YwU6BcROGIVtIvKjeVbd4IINE= generated-by-azure'
            }
          ]
        }
        provisionVMAgent: true
        patchSettings: {
          patchMode: 'ImageDefault'
          assessmentMode: 'ImageDefault'
        }
        enableVMAgentPlatformUpdates: true
      }
      secrets: []
      allowExtensionOperations: true
      requireGuestProvisionSignal: true
      adminUsername: 'azureuser'
    }
    securityProfile: {
      uefiSettings: {
        secureBootEnabled: true
        vTpmEnabled: true
      }
      securityType: 'TrustedLaunch'
    }
    networkProfile: {
      networkInterfaces: [
        {
          id: networkInterfaces_truman_control_center992_z1_name_resource.id
          properties: {
            deleteOption: 'Detach'
          }
        }
      ]
    }
    diagnosticsProfile: {
      bootDiagnostics: {
        enabled: true
      }
    }
  }
}

resource virtualMachines_truman_worker_1_name_resource 'Microsoft.Compute/virtualMachines@2025-04-01' = {
  name: virtualMachines_truman_worker_1_name
  location: 'northeurope'
  zones: [
    '1'
  ]
  properties: {
    hardwareProfile: {
      vmSize: 'Standard_D4as_v5'
    }
    additionalCapabilities: {
      hibernationEnabled: false
    }
    storageProfile: {
      imageReference: {
        publisher: 'canonical'
        offer: 'ubuntu-24_04-lts'
        sku: 'server'
        version: 'latest'
      }
      osDisk: {
        osType: 'Linux'
        name: '${virtualMachines_truman_worker_1_name}_OsDisk_1_4aaa02a95282493ebc608bca4a0e741e'
        createOption: 'FromImage'
        caching: 'ReadWrite'
        managedDisk: {
          storageAccountType: 'Premium_LRS'
          id: resourceId(
            'Microsoft.Compute/disks',
            '${virtualMachines_truman_worker_1_name}_OsDisk_1_4aaa02a95282493ebc608bca4a0e741e'
          )
        }
        deleteOption: 'Delete'
        diskSizeGB: 128
      }
      dataDisks: []
      diskControllerType: 'SCSI'
    }
    osProfile: {
      computerName: virtualMachines_truman_worker_1_name
      linuxConfiguration: {
        disablePasswordAuthentication: true
        ssh: {
          publicKeys: [
            {
              path: '/home/azureuser/.ssh/authorized_keys'
              keyData: 'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQDMEzV2TGLHxjFnW6TtzSZGShPnUcc7ktzwPyUZHK+dK/wja3Q7mA1ClKMLkKvMwuyzhocpua4dGYUUfTMeHAC5SXlIsHn5uEeH/qFoCzBlBmBHFVuRMAraf7X8do04cCB04dJR7rmbFor4UTZ92XJhUxG0rK6MFjcWSWR+Q8TIcBChFz6eVphWePHBEAcGsHJQ/JuMSnERSpF72UqIfjdVrgPsH+ezrbAGvhyRfYb+vDwMUPY3bY/o7o9hZhjQF3pSKbv0BWB77I2IXZKhJiD14KJP87lcmp6LruvxZtVJ8h8u298e1Zu2f1P505FgBv/zoILxBMgGUfnqjCRdf2RSTUKcVQBr1m3cM/ZqAY0vgJ9nQAJs+D5yXOZra1PuCTtg3xpZszUqqpmgMtuBqzf1u8BfShSJE8zKqJPI2rDNBsgAZ7jlP6Ze1CPgzy0/YcAYPaJBnWHfIY6EYG7uBpsx21cp33LqNeG5nK5d9nmRD9WgUWWVmq8Qv1aJ1b0C3IU= generated-by-azure'
            }
          ]
        }
        provisionVMAgent: true
        patchSettings: {
          patchMode: 'ImageDefault'
          assessmentMode: 'ImageDefault'
        }
        enableVMAgentPlatformUpdates: true
      }
      secrets: []
      allowExtensionOperations: true
      requireGuestProvisionSignal: true
      adminUsername: 'azureuser'
    }
    securityProfile: {
      uefiSettings: {
        secureBootEnabled: true
        vTpmEnabled: true
      }
      securityType: 'TrustedLaunch'
    }
    networkProfile: {
      networkInterfaces: [
        {
          id: networkInterfaces_truman_worker_1335_z1_name_resource.id
          properties: {
            deleteOption: 'Delete'
          }
        }
      ]
    }
  }
}

resource virtualMachines_truman_control_center_name_enablevmAccess 'Microsoft.Compute/virtualMachines/extensions@2025-04-01' = {
  parent: virtualMachines_truman_control_center_name_resource
  name: 'enablevmAccess'
  location: 'northeurope'
  properties: {
    autoUpgradeMinorVersion: true
    publisher: 'Microsoft.OSTCExtensions'
    type: 'VMAccessForLinux'
    typeHandlerVersion: '1.5'
    settings: {}
    protectedSettings: {
      username: extensions_enablevmAccess_username
      password: extensions_enablevmAccess_password
      ssh_key: extensions_enablevmAccess_ssh_key
      reset_ssh: extensions_enablevmAccess_reset_ssh
      remove_user: extensions_enablevmAccess_remove_user
      expiration: extensions_enablevmAccess_expiration
    }
  }
}

resource virtualMachines_truman_worker_1_name_enablevmAccess 'Microsoft.Compute/virtualMachines/extensions@2025-04-01' = {
  parent: virtualMachines_truman_worker_1_name_resource
  name: 'enablevmAccess'
  location: 'northeurope'
  properties: {
    autoUpgradeMinorVersion: true
    publisher: 'Microsoft.OSTCExtensions'
    type: 'VMAccessForLinux'
    typeHandlerVersion: '1.5'
    settings: {}
    protectedSettings: {
      username: extensions_enablevmAccess_username_1
      password: extensions_enablevmAccess_password_1
      ssh_key: extensions_enablevmAccess_ssh_key_1
      reset_ssh: extensions_enablevmAccess_reset_ssh_1
      remove_user: extensions_enablevmAccess_remove_user_1
      expiration: extensions_enablevmAccess_expiration_1
    }
  }
}

resource networkInterfaces_truman_worker_1335_z1_name_resource 'Microsoft.Network/networkInterfaces@2025-05-01' = {
  name: networkInterfaces_truman_worker_1335_z1_name
  location: 'northeurope'
  kind: 'Regular'
  properties: {
    ipConfigurations: [
      {
        name: 'ipconfig1'
        id: '${networkInterfaces_truman_worker_1335_z1_name_resource.id}/ipConfigurations/ipconfig1'
        type: 'Microsoft.Network/networkInterfaces/ipConfigurations'
        properties: {
          privateIPAddress: '10.2.1.4'
          privateIPAllocationMethod: 'Dynamic'
          subnet: {
            id: virtualNetworks_truman_control_center_vnet_name_agent_worker_subnet.id
          }
          primary: true
          privateIPAddressVersion: 'IPv4'
        }
      }
    ]
    dnsSettings: {
      dnsServers: []
    }
    enableAcceleratedNetworking: true
    enableIPForwarding: false
    disableTcpStateTracking: false
    nicType: 'Standard'
    auxiliaryMode: 'None'
    auxiliarySku: 'None'
  }
}

resource networkSecurityGroups_nsg_agent_worker_subnet_name_AllowALB 'Microsoft.Network/networkSecurityGroups/securityRules@2025-05-01' = {
  name: '${networkSecurityGroups_nsg_agent_worker_subnet_name}/AllowALB'
  properties: {
    protocol: '*'
    sourcePortRange: '*'
    destinationPortRange: '*'
    sourceAddressPrefix: 'AzureLoadBalancer'
    destinationAddressPrefix: '*'
    access: 'Allow'
    priority: 300
    direction: 'Inbound'
    sourcePortRanges: []
    destinationPortRanges: []
    sourceAddressPrefixes: []
    destinationAddressPrefixes: []
  }
  dependsOn: [
    networkSecurityGroups_nsg_agent_worker_subnet_name_resource
  ]
}

resource networkSecurityGroups_nsg_agent_worker_subnet_name_Allow_ControlPlane_WorkerDaemon_11888 'Microsoft.Network/networkSecurityGroups/securityRules@2025-05-01' = {
  name: '${networkSecurityGroups_nsg_agent_worker_subnet_name}/Allow-ControlPlane-WorkerDaemon-11888'
  properties: {
    protocol: 'TCP'
    sourcePortRange: '*'
    sourceAddressPrefix: '10.2.0.4/32'
    destinationAddressPrefix: '*'
    access: 'Allow'
    priority: 200
    direction: 'Inbound'
    sourcePortRanges: []
    destinationPortRanges: [
      '11888'
      '22'
    ]
    sourceAddressPrefixes: []
    destinationAddressPrefixes: []
  }
  dependsOn: [
    networkSecurityGroups_nsg_agent_worker_subnet_name_resource
  ]
}

resource networkSecurityGroups_nsg_agent_worker_subnet_name_Deny_Internet 'Microsoft.Network/networkSecurityGroups/securityRules@2025-05-01' = {
  name: '${networkSecurityGroups_nsg_agent_worker_subnet_name}/Deny_Internet'
  properties: {
    protocol: '*'
    sourcePortRange: '*'
    destinationPortRange: '*'
    sourceAddressPrefix: '*'
    destinationAddressPrefix: 'Internet'
    access: 'Deny'
    priority: 210
    direction: 'Outbound'
    sourcePortRanges: []
    destinationPortRanges: []
    sourceAddressPrefixes: []
    destinationAddressPrefixes: []
  }
  dependsOn: [
    networkSecurityGroups_nsg_agent_worker_subnet_name_resource
  ]
}

resource networkSecurityGroups_nsg_agent_worker_subnet_name_DenyAllInbound 'Microsoft.Network/networkSecurityGroups/securityRules@2025-05-01' = {
  name: '${networkSecurityGroups_nsg_agent_worker_subnet_name}/DenyAllInbound'
  properties: {
    protocol: '*'
    sourcePortRange: '*'
    destinationPortRange: '*'
    sourceAddressPrefix: '*'
    destinationAddressPrefix: '*'
    access: 'Deny'
    priority: 400
    direction: 'Inbound'
    sourcePortRanges: []
    destinationPortRanges: []
    sourceAddressPrefixes: []
    destinationAddressPrefixes: []
  }
  dependsOn: [
    networkSecurityGroups_nsg_agent_worker_subnet_name_resource
  ]
}

resource networkSecurityGroups_truman_control_center_nsg_name_DenyWorkerInbound 'Microsoft.Network/networkSecurityGroups/securityRules@2025-05-01' = {
  name: '${networkSecurityGroups_truman_control_center_nsg_name}/DenyWorkerInbound'
  properties: {
    protocol: '*'
    sourcePortRange: '*'
    destinationPortRange: '*'
    sourceAddressPrefix: '10.2.1.0/27'
    destinationAddressPrefix: '*'
    access: 'Deny'
    priority: 340
    direction: 'Inbound'
    sourcePortRanges: []
    destinationPortRanges: []
    sourceAddressPrefixes: []
    destinationAddressPrefixes: []
  }
  dependsOn: [
    networkSecurityGroups_truman_control_center_nsg_name_resource
  ]
}

resource networkSecurityGroups_truman_control_center_nsg_name_HTTP 'Microsoft.Network/networkSecurityGroups/securityRules@2025-05-01' = {
  name: '${networkSecurityGroups_truman_control_center_nsg_name}/HTTP'
  properties: {
    protocol: 'TCP'
    sourcePortRange: '*'
    destinationPortRange: '80'
    sourceAddressPrefix: '*'
    destinationAddressPrefix: '*'
    access: 'Allow'
    priority: 320
    direction: 'Inbound'
    sourcePortRanges: []
    destinationPortRanges: []
    sourceAddressPrefixes: []
    destinationAddressPrefixes: []
  }
  dependsOn: [
    networkSecurityGroups_truman_control_center_nsg_name_resource
  ]
}

resource networkSecurityGroups_truman_control_center_nsg_name_HTTPS 'Microsoft.Network/networkSecurityGroups/securityRules@2025-05-01' = {
  name: '${networkSecurityGroups_truman_control_center_nsg_name}/HTTPS'
  properties: {
    protocol: 'TCP'
    sourcePortRange: '*'
    destinationPortRange: '443'
    sourceAddressPrefix: '*'
    destinationAddressPrefix: '*'
    access: 'Allow'
    priority: 310
    direction: 'Inbound'
    sourcePortRanges: []
    destinationPortRanges: []
    sourceAddressPrefixes: []
    destinationAddressPrefixes: []
  }
  dependsOn: [
    networkSecurityGroups_truman_control_center_nsg_name_resource
  ]
}

resource networkSecurityGroups_truman_control_center_nsg_name_SSH 'Microsoft.Network/networkSecurityGroups/securityRules@2025-05-01' = {
  name: '${networkSecurityGroups_truman_control_center_nsg_name}/SSH'
  properties: {
    protocol: 'TCP'
    sourcePortRange: '*'
    destinationPortRange: '22'
    sourceAddressPrefix: '90.224.191.157'
    destinationAddressPrefix: '*'
    access: 'Allow'
    priority: 300
    direction: 'Inbound'
    sourcePortRanges: []
    destinationPortRanges: []
    sourceAddressPrefixes: []
    destinationAddressPrefixes: []
  }
  dependsOn: [
    networkSecurityGroups_truman_control_center_nsg_name_resource
  ]
}

resource networkSecurityGroups_truman_control_center_nsg_name_WorkerPorts 'Microsoft.Network/networkSecurityGroups/securityRules@2025-05-01' = {
  name: '${networkSecurityGroups_truman_control_center_nsg_name}/WorkerPorts'
  properties: {
    protocol: 'TCP'
    sourcePortRange: '*'
    sourceAddressPrefix: '10.2.1.0/27'
    destinationAddressPrefix: '*'
    access: 'Allow'
    priority: 330
    direction: 'Inbound'
    sourcePortRanges: []
    destinationPortRanges: [
      '22'
      '8000'
      '11434'
      '11666'
      '11777'
      '8081'
      '3142'
    ]
    sourceAddressPrefixes: []
    destinationAddressPrefixes: []
  }
  dependsOn: [
    networkSecurityGroups_truman_control_center_nsg_name_resource
  ]
}

resource virtualNetworks_truman_control_center_vnet_name_resource 'Microsoft.Network/virtualNetworks@2025-05-01' = {
  name: virtualNetworks_truman_control_center_vnet_name
  location: 'northeurope'
  properties: {
    addressSpace: {
      addressPrefixes: [
        '10.2.0.0/16'
      ]
    }
    privateEndpointVNetPolicies: 'Disabled'
    subnets: [
      {
        name: 'default'
        id: virtualNetworks_truman_control_center_vnet_name_default.id
        properties: {
          addressPrefix: '10.2.0.0/24'
          delegations: []
          privateEndpointNetworkPolicies: 'Disabled'
          privateLinkServiceNetworkPolicies: 'Enabled'
        }
        type: 'Microsoft.Network/virtualNetworks/subnets'
      }
      {
        name: 'agent-worker-subnet'
        id: virtualNetworks_truman_control_center_vnet_name_agent_worker_subnet.id
        properties: {
          addressPrefix: '10.2.1.0/27'
          networkSecurityGroup: {
            id: networkSecurityGroups_nsg_agent_worker_subnet_name_resource.id
          }
          delegations: []
          privateEndpointNetworkPolicies: 'Disabled'
          privateLinkServiceNetworkPolicies: 'Enabled'
          defaultOutboundAccess: false
        }
        type: 'Microsoft.Network/virtualNetworks/subnets'
      }
    ]
    virtualNetworkPeerings: []
    enableDdosProtection: false
  }
}

resource virtualNetworks_truman_control_center_vnet_name_default 'Microsoft.Network/virtualNetworks/subnets@2025-05-01' = {
  name: '${virtualNetworks_truman_control_center_vnet_name}/default'
  properties: {
    addressPrefix: '10.2.0.0/24'
    delegations: []
    privateEndpointNetworkPolicies: 'Disabled'
    privateLinkServiceNetworkPolicies: 'Enabled'
  }
  dependsOn: [
    virtualNetworks_truman_control_center_vnet_name_resource
  ]
}

resource virtualNetworks_truman_control_center_vnet_name_agent_worker_subnet 'Microsoft.Network/virtualNetworks/subnets@2025-05-01' = {
  name: '${virtualNetworks_truman_control_center_vnet_name}/agent-worker-subnet'
  properties: {
    addressPrefix: '10.2.1.0/27'
    networkSecurityGroup: {
      id: networkSecurityGroups_nsg_agent_worker_subnet_name_resource.id
    }
    delegations: []
    privateEndpointNetworkPolicies: 'Disabled'
    privateLinkServiceNetworkPolicies: 'Enabled'
    defaultOutboundAccess: false
  }
  dependsOn: [
    virtualNetworks_truman_control_center_vnet_name_resource
  ]
}

resource networkInterfaces_truman_control_center992_z1_name_resource 'Microsoft.Network/networkInterfaces@2025-05-01' = {
  name: networkInterfaces_truman_control_center992_z1_name
  location: 'northeurope'
  kind: 'Regular'
  properties: {
    ipConfigurations: [
      {
        name: 'ipconfig1'
        id: '${networkInterfaces_truman_control_center992_z1_name_resource.id}/ipConfigurations/ipconfig1'
        type: 'Microsoft.Network/networkInterfaces/ipConfigurations'
        properties: {
          privateIPAddress: '10.2.0.4'
          privateIPAllocationMethod: 'Dynamic'
          publicIPAddress: {
            id: publicIPAddresses_truman_control_center_ip_name_resource.id
            properties: {
              deleteOption: 'Detach'
            }
          }
          subnet: {
            id: virtualNetworks_truman_control_center_vnet_name_default.id
          }
          primary: true
          privateIPAddressVersion: 'IPv4'
        }
      }
    ]
    dnsSettings: {
      dnsServers: []
    }
    enableAcceleratedNetworking: true
    enableIPForwarding: false
    disableTcpStateTracking: false
    networkSecurityGroup: {
      id: networkSecurityGroups_truman_control_center_nsg_name_resource.id
    }
    nicType: 'Standard'
    auxiliaryMode: 'None'
    auxiliarySku: 'None'
  }
}
